# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

from tests import unittest
from dateutil.tz import tzutc, tzoffset
import datetime
import six

import mock

from botocore import xform_name
from botocore.compat import OrderedDict
from botocore.awsrequest import AWSRequest
from botocore.exceptions import InvalidExpressionError, ConfigNotFound
from botocore.exceptions import InvalidDNSNameError
from botocore.model import ServiceModel
from botocore.utils import remove_dot_segments
from botocore.utils import normalize_url_path
from botocore.utils import validate_jmespath_for_set
from botocore.utils import set_value_from_jmespath
from botocore.utils import parse_key_val_file_contents
from botocore.utils import parse_key_val_file
from botocore.utils import parse_timestamp
from botocore.utils import parse_to_aware_datetime
from botocore.utils import CachedProperty
from botocore.utils import ArgumentGenerator
from botocore.utils import calculate_tree_hash
from botocore.utils import calculate_sha256
from botocore.utils import is_valid_endpoint_url
from botocore.utils import fix_s3_host
from botocore.utils import switch_to_virtual_host_style
from botocore.utils import instance_cache
from botocore.utils import merge_dicts
from botocore.utils import get_service_module_name
from botocore.utils import percent_encode_sequence
from botocore.model import DenormalizedStructureBuilder
from botocore.model import ShapeResolver


class TestURINormalization(unittest.TestCase):
    def test_remove_dot_segments(self):
        self.assertEqual(remove_dot_segments('../foo'), 'foo')
        self.assertEqual(remove_dot_segments('../../foo'), 'foo')
        self.assertEqual(remove_dot_segments('./foo'), 'foo')
        self.assertEqual(remove_dot_segments('/./'), '/')
        self.assertEqual(remove_dot_segments('/../'), '/')
        self.assertEqual(remove_dot_segments('/foo/bar/baz/../qux'),
                         '/foo/bar/qux')
        self.assertEqual(remove_dot_segments('/foo/..'), '/')
        self.assertEqual(remove_dot_segments('foo/bar/baz'), 'foo/bar/baz')
        self.assertEqual(remove_dot_segments('..'), '')
        self.assertEqual(remove_dot_segments('.'), '')
        self.assertEqual(remove_dot_segments('/.'), '/')
        # I don't think this is RFC compliant...
        self.assertEqual(remove_dot_segments('//foo//'), '/foo/')

    def test_empty_url_normalization(self):
        self.assertEqual(normalize_url_path(''), '/')


class TestTransformName(unittest.TestCase):
    def test_upper_camel_case(self):
        self.assertEqual(xform_name('UpperCamelCase'), 'upper_camel_case')
        self.assertEqual(xform_name('UpperCamelCase', '-'), 'upper-camel-case')

    def test_lower_camel_case(self):
        self.assertEqual(xform_name('lowerCamelCase'), 'lower_camel_case')
        self.assertEqual(xform_name('lowerCamelCase', '-'), 'lower-camel-case')

    def test_consecutive_upper_case(self):
        self.assertEqual(xform_name('HTTPHeaders'), 'http_headers')
        self.assertEqual(xform_name('HTTPHeaders', '-'), 'http-headers')

    def test_consecutive_upper_case_middle_string(self):
        self.assertEqual(xform_name('MainHTTPHeaders'), 'main_http_headers')
        self.assertEqual(xform_name('MainHTTPHeaders', '-'), 'main-http-headers')

    def test_s3_prefix(self):
        self.assertEqual(xform_name('S3BucketName'), 's3_bucket_name')

    def test_already_snake_cased(self):
        self.assertEqual(xform_name('leave_alone'), 'leave_alone')
        self.assertEqual(xform_name('s3_bucket_name'), 's3_bucket_name')
        self.assertEqual(xform_name('bucket_s3_name'), 'bucket_s3_name')

    def test_special_cases(self):
        # Some patterns don't actually match the rules we expect.
        self.assertEqual(xform_name('SwapEnvironmentCNAMEs'), 'swap_environment_cnames')
        self.assertEqual(xform_name('SwapEnvironmentCNAMEs', '-'), 'swap-environment-cnames')
        self.assertEqual(xform_name('CreateCachediSCSIVolume', '-'), 'create-cached-iscsi-volume')
        self.assertEqual(xform_name('DescribeCachediSCSIVolumes', '-'), 'describe-cached-iscsi-volumes')
        self.assertEqual(xform_name('DescribeStorediSCSIVolumes', '-'), 'describe-stored-iscsi-volumes')
        self.assertEqual(xform_name('CreateStorediSCSIVolume', '-'), 'create-stored-iscsi-volume')


class TestValidateJMESPathForSet(unittest.TestCase):
    def setUp(self):
        super(TestValidateJMESPathForSet, self).setUp()
        self.data = {
            'Response': {
                'Thing': {
                    'Id': 1,
                    'Name': 'Thing #1',
                }
            },
            'Marker': 'some-token'
        }

    def test_invalid_exp(self):
        with self.assertRaises(InvalidExpressionError):
            validate_jmespath_for_set('Response.*.Name')

        with self.assertRaises(InvalidExpressionError):
            validate_jmespath_for_set('Response.Things[0]')

        with self.assertRaises(InvalidExpressionError):
            validate_jmespath_for_set('')

        with self.assertRaises(InvalidExpressionError):
            validate_jmespath_for_set('.')


class TestSetValueFromJMESPath(unittest.TestCase):
    def setUp(self):
        super(TestSetValueFromJMESPath, self).setUp()
        self.data = {
            'Response': {
                'Thing': {
                    'Id': 1,
                    'Name': 'Thing #1',
                }
            },
            'Marker': 'some-token'
        }

    def test_single_depth_existing(self):
        set_value_from_jmespath(self.data, 'Marker', 'new-token')
        self.assertEqual(self.data['Marker'], 'new-token')

    def test_single_depth_new(self):
        self.assertFalse('Limit' in self.data)
        set_value_from_jmespath(self.data, 'Limit', 100)
        self.assertEqual(self.data['Limit'], 100)

    def test_multiple_depth_existing(self):
        set_value_from_jmespath(self.data, 'Response.Thing.Name', 'New Name')
        self.assertEqual(self.data['Response']['Thing']['Name'], 'New Name')

    def test_multiple_depth_new(self):
        self.assertFalse('Brand' in self.data)
        set_value_from_jmespath(self.data, 'Brand.New', {'abc': 123})
        self.assertEqual(self.data['Brand']['New']['abc'], 123)


class TestParseEC2CredentialsFile(unittest.TestCase):
    def test_parse_ec2_content(self):
        contents = "AWSAccessKeyId=a\nAWSSecretKey=b\n"
        self.assertEqual(parse_key_val_file_contents(contents),
                         {'AWSAccessKeyId': 'a',
                          'AWSSecretKey': 'b'})

    def test_parse_ec2_content_empty(self):
        contents = ""
        self.assertEqual(parse_key_val_file_contents(contents), {})

    def test_key_val_pair_with_blank_lines(self):
        # The \n\n has an extra blank between the access/secret keys.
        contents = "AWSAccessKeyId=a\n\nAWSSecretKey=b\n"
        self.assertEqual(parse_key_val_file_contents(contents),
                         {'AWSAccessKeyId': 'a',
                          'AWSSecretKey': 'b'})

    def test_key_val_parser_lenient(self):
        # Ignore any line that does not have a '=' char in it.
        contents = "AWSAccessKeyId=a\nNOTKEYVALLINE\nAWSSecretKey=b\n"
        self.assertEqual(parse_key_val_file_contents(contents),
                         {'AWSAccessKeyId': 'a',
                          'AWSSecretKey': 'b'})

    def test_multiple_equals_on_line(self):
        contents = "AWSAccessKeyId=a\nAWSSecretKey=secret_key_with_equals=b\n"
        self.assertEqual(parse_key_val_file_contents(contents),
                         {'AWSAccessKeyId': 'a',
                          'AWSSecretKey': 'secret_key_with_equals=b'})

    def test_os_error_raises_config_not_found(self):
        mock_open = mock.Mock()
        mock_open.side_effect = OSError()
        with self.assertRaises(ConfigNotFound):
            parse_key_val_file('badfile', _open=mock_open)


class TestParseTimestamps(unittest.TestCase):
    def test_parse_iso8601(self):
        self.assertEqual(
            parse_timestamp('1970-01-01T00:10:00.000Z'),
            datetime.datetime(1970, 1, 1, 0, 10, tzinfo=tzutc()))

    def test_parse_epoch(self):
        self.assertEqual(
            parse_timestamp(1222172800),
            datetime.datetime(2008, 9, 23, 12, 26, 40, tzinfo=tzutc()))

    def test_parse_epoch_zero_time(self):
        self.assertEqual(
            parse_timestamp(0),
            datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc()))

    def test_parse_epoch_as_string(self):
        self.assertEqual(
            parse_timestamp('1222172800'),
            datetime.datetime(2008, 9, 23, 12, 26, 40, tzinfo=tzutc()))

    def test_parse_rfc822(self):
        self.assertEqual(
            parse_timestamp('Wed, 02 Oct 2002 13:00:00 GMT'),
            datetime.datetime(2002, 10, 2, 13, 0, tzinfo=tzutc()))

    def test_parse_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            parse_timestamp('invalid date')


class TestParseToUTCDatetime(unittest.TestCase):
    def test_handles_utc_time(self):
        original = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(parse_to_aware_datetime(original), original)

    def test_handles_other_timezone(self):
        tzinfo = tzoffset("BRST", -10800)
        original = datetime.datetime(2014, 1, 1, 0, 0, 0, tzinfo=tzinfo)
        self.assertEqual(parse_to_aware_datetime(original), original)

    def test_handles_naive_datetime(self):
        original = datetime.datetime(1970, 1, 1, 0, 0, 0)
        expected = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(parse_to_aware_datetime(original), expected)

    def test_handles_string_epoch(self):
        expected = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(parse_to_aware_datetime('0'), expected)

    def test_handles_int_epoch(self):
        expected = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(parse_to_aware_datetime(0), expected)

    def test_handles_full_iso_8601(self):
        expected = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(
            parse_to_aware_datetime('1970-01-01T00:00:00Z'),
            expected)

    def test_year_only_iso_8601(self):
        expected = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=tzutc())
        self.assertEqual(parse_to_aware_datetime('1970-01-01'), expected)


class TestCachedProperty(unittest.TestCase):
    def test_cached_property_same_value(self):
        class CacheMe(object):
            @CachedProperty
            def foo(self):
                return 'foo'

        c = CacheMe()
        self.assertEqual(c.foo, 'foo')
        self.assertEqual(c.foo, 'foo')

    def test_cached_property_only_called_once(self):
        # Note: you would normally never want to cache
        # a property that returns a new value each time,
        # but this is done to demonstrate the caching behavior.

        class NoIncrement(object):
            def __init__(self):
                self.counter = 0

            @CachedProperty
            def current_value(self):
                self.counter += 1
                return self.counter

        c = NoIncrement()
        self.assertEqual(c.current_value, 1)
        # If the property wasn't cached, the next value should be
        # be 2, but because it's cached, we know the value will be 1.
        self.assertEqual(c.current_value, 1)


class TestArgumentGenerator(unittest.TestCase):
    def setUp(self):
        self.arg_generator = ArgumentGenerator()

    def assert_skeleton_from_model_is(self, model, generated_skeleton):
        shape = DenormalizedStructureBuilder().with_members(
            model).build_model()
        actual = self.arg_generator.generate_skeleton(shape)
        self.assertEqual(actual, generated_skeleton)

    def test_generate_string(self):
        self.assert_skeleton_from_model_is(
            model={
                'A': {'type': 'string'}
            },
            generated_skeleton={
                'A': ''
            }
        )

    def test_generate_scalars(self):
        self.assert_skeleton_from_model_is(
            model={
                'A': {'type': 'string'},
                'B': {'type': 'integer'},
                'C': {'type': 'float'},
                'D': {'type': 'boolean'},
            },
            generated_skeleton={
                'A': '',
                'B': 0,
                'C': 0.0,
                'D': True,
            }
        )

    def test_generate_nested_structure(self):
        self.assert_skeleton_from_model_is(
            model={
                'A': {
                    'type': 'structure',
                    'members': {
                        'B': {'type': 'string'},
                    }
                }
            },
            generated_skeleton={
                'A': {'B': ''}
            }
        )

    def test_generate_scalar_list(self):
        self.assert_skeleton_from_model_is(
            model={
                'A': {
                    'type': 'list',
                    'member': {
                        'type': 'string'
                    }
                },
            },
            generated_skeleton={
                'A': [''],
            }
        )

    def test_generate_scalar_map(self):
        self.assert_skeleton_from_model_is(
            model={
                'A': {
                    'type': 'map',
                    'key': {'type': 'string'},
                    'value':  {'type': 'string'},
                }
            },
            generated_skeleton={
                'A': {
                    'KeyName': '',
                }
            }
        )

    def test_handles_recursive_shapes(self):
        # We're not using assert_skeleton_from_model_is
        # because we can't use a DenormalizedStructureBuilder,
        # we need a normalized model to represent recursive
        # shapes.
        shape_map = ShapeResolver({
            'InputShape': {
                'type': 'structure',
                'members': {
                    'A': {'shape': 'RecursiveStruct'},
                    'B': {'shape': 'StringType'},
                }
            },
            'RecursiveStruct': {
                'type': 'structure',
                'members': {
                    'C': {'shape': 'RecursiveStruct'},
                    'D': {'shape': 'StringType'},
                }
            },
            'StringType': {
                'type': 'string',
            }
        })
        shape = shape_map.get_shape_by_name('InputShape')
        actual = self.arg_generator.generate_skeleton(shape)
        expected = {
            'A': {
                'C': {
                    # For recurisve shapes, we'll just show
                    # an empty dict.
                },
                'D': ''
            },
            'B': ''
        }
        self.assertEqual(actual, expected)


class TestChecksums(unittest.TestCase):
    def test_empty_hash(self):
        self.assertEqual(
            calculate_sha256(six.BytesIO(b''), as_hex=True),
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_as_hex(self):
        self.assertEqual(
            calculate_sha256(six.BytesIO(b'hello world'), as_hex=True),
            'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9')

    def test_as_binary(self):
        self.assertEqual(
            calculate_sha256(six.BytesIO(b'hello world'), as_hex=False),
            (b"\xb9M'\xb9\x93M>\x08\xa5.R\xd7\xda}\xab\xfa\xc4\x84\xef"
             b"\xe3zS\x80\xee\x90\x88\xf7\xac\xe2\xef\xcd\xe9"))


class TestTreeHash(unittest.TestCase):
    # Note that for these tests I've independently verified
    # what the expected tree hashes should be from other
    # SDK implementations.

    def test_empty_tree_hash(self):
        self.assertEqual(
            calculate_tree_hash(six.BytesIO(b'')),
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_tree_hash_less_than_one_mb(self):
        one_k = six.BytesIO(b'a' * 1024)
        self.assertEqual(
            calculate_tree_hash(one_k),
            '2edc986847e209b4016e141a6dc8716d3207350f416969382d431539bf292e4a')

    def test_tree_hash_exactly_one_mb(self):
        one_meg_bytestring = b'a' * (1 * 1024 * 1024)
        one_meg = six.BytesIO(one_meg_bytestring)
        self.assertEqual(
            calculate_tree_hash(one_meg),
            '9bc1b2a288b26af7257a36277ae3816a7d4f16e89c1e7e77d0a5c48bad62b360')

    def test_tree_hash_multiple_of_one_mb(self):
        four_mb = six.BytesIO(b'a' * (4 * 1024 * 1024))
        self.assertEqual(
            calculate_tree_hash(four_mb),
            '9491cb2ed1d4e7cd53215f4017c23ec4ad21d7050a1e6bb636c4f67e8cddb844')

    def test_tree_hash_offset_of_one_mb_multiple(self):
        offset_four_mb = six.BytesIO(b'a' * (4 * 1024 * 1024) + b'a' * 20)
        self.assertEqual(
            calculate_tree_hash(offset_four_mb),
            '12f3cbd6101b981cde074039f6f728071da8879d6f632de8afc7cdf00661b08f')


class TestIsValidEndpointURL(unittest.TestCase):
    def test_dns_name_is_valid(self):
        self.assertTrue(is_valid_endpoint_url('https://s3.amazonaws.com/'))

    def test_ip_address_is_allowed(self):
        self.assertTrue(is_valid_endpoint_url('https://10.10.10.10/'))

    def test_path_component_ignored(self):
        self.assertTrue(
            is_valid_endpoint_url('https://foo.bar.com/other/path/'))

    def test_can_have_port(self):
        self.assertTrue(is_valid_endpoint_url('https://foo.bar.com:12345/'))

    def test_ip_can_have_port(self):
        self.assertTrue(is_valid_endpoint_url('https://10.10.10.10:12345/'))

    def test_cannot_have_spaces(self):
        self.assertFalse(is_valid_endpoint_url('https://my invalid name/'))

    def test_missing_scheme(self):
        self.assertFalse(is_valid_endpoint_url('foo.bar.com'))

    def test_no_new_lines(self):
        self.assertFalse(is_valid_endpoint_url('https://foo.bar.com\nbar/'))

    def test_long_hostname(self):
        long_hostname = 'htps://%s.com' % ('a' * 256)
        self.assertFalse(is_valid_endpoint_url(long_hostname))

    def test_hostname_can_end_with_dot(self):
        self.assertTrue(is_valid_endpoint_url('https://foo.bar.com./'))

    def test_hostname_no_dots(self):
        self.assertTrue(is_valid_endpoint_url('https://foo/'))


class TestFixS3Host(unittest.TestCase):
    def test_fix_s3_host_initial(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://s3-us-west-2.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        fix_s3_host(
            request=request, signature_version=signature_version,
            region_name=region_name)
        self.assertEqual(request.url,
                         'https://bucket.s3.amazonaws.com/key.txt')
        self.assertEqual(request.auth_path, '/bucket/key.txt')

    def test_fix_s3_host_only_applied_once(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://s3-us-west-2.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        fix_s3_host(
            request=request, signature_version=signature_version,
            region_name=region_name)
        # Calling the handler again should not affect the end result:
        fix_s3_host(
            request=request, signature_version=signature_version,
            region_name=region_name)
        self.assertEqual(request.url,
                         'https://bucket.s3.amazonaws.com/key.txt')
        # This was a bug previously.  We want to make sure that
        # calling fix_s3_host() again does not alter the auth_path.
        # Otherwise we'll get signature errors.
        self.assertEqual(request.auth_path, '/bucket/key.txt')

    def test_dns_style_not_used_for_get_bucket_location(self):
        original_url = 'https://s3-us-west-2.amazonaws.com/bucket?location'
        request = AWSRequest(
            method='GET', headers={},
            url=original_url,
        )
        signature_version = 's3'
        region_name = 'us-west-2'
        fix_s3_host(
            request=request, signature_version=signature_version,
            region_name=region_name)
        # The request url should not have been modified because this is
        # a request for GetBucketLocation.
        self.assertEqual(request.url, original_url)


class TestSwitchToVirtualHostStyle(unittest.TestCase):
    def test_switch_to_virtual_host_style(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        self.assertEqual(request.url,
                         'https://bucket.foo.amazonaws.com/key.txt')
        self.assertEqual(request.auth_path, '/bucket/key.txt')

    def test_uses_default_endpoint(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name, default_endpoint_url='s3.amazonaws.com')
        self.assertEqual(request.url,
                         'https://bucket.s3.amazonaws.com/key.txt')
        self.assertEqual(request.auth_path, '/bucket/key.txt')

    def test_throws_invalid_dns_name_error(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/mybucket.foo/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        with self.assertRaises(InvalidDNSNameError):
            switch_to_virtual_host_style(
                request=request, signature_version=signature_version,
                region_name=region_name)

    def test_fix_s3_host_only_applied_once(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        # Calling the handler again should not affect the end result:
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        self.assertEqual(request.url,
                         'https://bucket.foo.amazonaws.com/key.txt')
        # This was a bug previously.  We want to make sure that
        # calling fix_s3_host() again does not alter the auth_path.
        # Otherwise we'll get signature errors.
        self.assertEqual(request.auth_path, '/bucket/key.txt')

    def test_virtual_host_style_for_make_bucket(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/bucket'
        )
        region_name = 'us-west-2'
        signature_version = 's3'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        self.assertEqual(request.url,
                         'https://bucket.foo.amazonaws.com/')

    def test_virtual_host_style_not_used_for_get_bucket_location(self):
        original_url = 'https://foo.amazonaws.com/bucket?location'
        request = AWSRequest(
            method='GET', headers={},
            url=original_url,
        )
        signature_version = 's3'
        region_name = 'us-west-2'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        # The request url should not have been modified because this is
        # a request for GetBucketLocation.
        self.assertEqual(request.url, original_url)

    def test_virtual_host_style_not_used_for_list_buckets(self):
        original_url = 'https://foo.amazonaws.com/'
        request = AWSRequest(
            method='GET', headers={},
            url=original_url,
        )
        signature_version = 's3'
        region_name = 'us-west-2'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name)
        # The request url should not have been modified because this is
        # a request for GetBucketLocation.
        self.assertEqual(request.url, original_url)

    def test_is_unaffected_by_sigv4(self):
        request = AWSRequest(
            method='PUT', headers={},
            url='https://foo.amazonaws.com/bucket/key.txt'
        )
        region_name = 'us-west-2'
        signature_version = 's3v4'
        switch_to_virtual_host_style(
            request=request, signature_version=signature_version,
            region_name=region_name, default_endpoint_url='s3.amazonaws.com')
        self.assertEqual(request.url,
                         'https://bucket.s3.amazonaws.com/key.txt')


class TestInstanceCache(unittest.TestCase):
    class DummyClass(object):
        def __init__(self, cache):
            self._instance_cache = cache

        @instance_cache
        def add(self, x, y):
            return x + y

        @instance_cache
        def sub(self, x, y):
            return x - y

    def setUp(self):
        self.cache = {}

    def test_cache_single_method_call(self):
        adder = self.DummyClass(self.cache)
        self.assertEqual(adder.add(2, 1), 3)
        # This should result in one entry in the cache.
        self.assertEqual(len(self.cache), 1)
        # When we call the method with the same args,
        # we should reuse the same entry in the cache.
        self.assertEqual(adder.add(2, 1), 3)
        self.assertEqual(len(self.cache), 1)

    def test_can_cache_multiple_methods(self):
        adder = self.DummyClass(self.cache)
        adder.add(2, 1)

        # A different method results in a new cache entry,
        # so now there should be two elements in the cache.
        self.assertEqual(adder.sub(2, 1), 1)
        self.assertEqual(len(self.cache), 2)
        self.assertEqual(adder.sub(2, 1), 1)

    def test_can_cache_kwargs(self):
        adder = self.DummyClass(self.cache)
        adder.add(x=2, y=1)
        self.assertEqual(adder.add(x=2, y=1), 3)
        self.assertEqual(len(self.cache), 1)


class TestMergeDicts(unittest.TestCase):
    def test_merge_dicts_overrides(self):
        first = {
            'foo': {'bar': {'baz': {'one': 'ORIGINAL', 'two': 'ORIGINAL'}}}}
        second = {'foo': {'bar': {'baz': {'one': 'UPDATE'}}}}

        merge_dicts(first, second)
        # The value from the second dict wins.
        self.assertEqual(first['foo']['bar']['baz']['one'], 'UPDATE')
        # And we still preserve the other attributes.
        self.assertEqual(first['foo']['bar']['baz']['two'], 'ORIGINAL')

    def test_merge_dicts_new_keys(self):
        first = {
            'foo': {'bar': {'baz': {'one': 'ORIGINAL', 'two': 'ORIGINAL'}}}}
        second = {'foo': {'bar': {'baz': {'three': 'UPDATE'}}}}

        merge_dicts(first, second)
        self.assertEqual(first['foo']['bar']['baz']['one'], 'ORIGINAL')
        self.assertEqual(first['foo']['bar']['baz']['two'], 'ORIGINAL')
        self.assertEqual(first['foo']['bar']['baz']['three'], 'UPDATE')

    def test_merge_empty_dict_does_nothing(self):
        first = {'foo': {'bar': 'baz'}}
        merge_dicts(first, {})
        self.assertEqual(first, {'foo': {'bar': 'baz'}})

    def test_more_than_one_sub_dict(self):
        first = {'one': {'inner': 'ORIGINAL', 'inner2': 'ORIGINAL'},
                 'two': {'inner': 'ORIGINAL', 'inner2': 'ORIGINAL'}}
        second = {'one': {'inner': 'UPDATE'}, 'two': {'inner': 'UPDATE'}}

        merge_dicts(first, second)
        self.assertEqual(first['one']['inner'], 'UPDATE')
        self.assertEqual(first['one']['inner2'], 'ORIGINAL')

        self.assertEqual(first['two']['inner'], 'UPDATE')
        self.assertEqual(first['two']['inner2'], 'ORIGINAL')

    def test_new_keys(self):
        first = {'one': {'inner': 'ORIGINAL'}, 'two': {'inner': 'ORIGINAL'}}
        second = {'three': {'foo': {'bar': 'baz'}}}
        # In this case, second has no keys in common, but we'd still expect
        # this to get merged.
        merge_dicts(first, second)
        self.assertEqual(first['three']['foo']['bar'], 'baz')

    def test_list_values_no_append(self):
        dict1 = {'Foo': ['old_foo_value']}
        dict2 = {'Foo': ['new_foo_value']}
        merge_dicts(dict1, dict2)
        self.assertEqual(
            dict1, {'Foo': ['new_foo_value']})

    def test_list_values_append(self):
        dict1 = {'Foo': ['old_foo_value']}
        dict2 = {'Foo': ['new_foo_value']}
        merge_dicts(dict1, dict2, append_lists=True)
        self.assertEqual(
            dict1, {'Foo': ['old_foo_value', 'new_foo_value']})

    def test_list_values_mismatching_types(self):
        dict1 = {'Foo': 'old_foo_value'}
        dict2 = {'Foo': ['new_foo_value']}
        merge_dicts(dict1, dict2, append_lists=True)
        self.assertEqual(
            dict1, {'Foo': ['new_foo_value']})

    def test_list_values_missing_key(self):
        dict1 = {}
        dict2 = {'Foo': ['foo_value']}
        merge_dicts(dict1, dict2, append_lists=True)
        self.assertEqual(
            dict1, {'Foo': ['foo_value']})


class TestGetServiceModuleName(unittest.TestCase):
    def setUp(self):
        self.service_description = {
            'metadata': {
                'serviceFullName': 'AWS MyService',
                'apiVersion': '2014-01-01',
                'endpointPrefix': 'myservice',
                'signatureVersion': 'v4',
                'protocol': 'query'
            },
            'operations': {},
            'shapes': {},
        }
        self.service_model = ServiceModel(
            self.service_description, 'myservice')

    def test_default(self):
        self.assertEqual(
            get_service_module_name(self.service_model),
            'MyService'
        )

    def test_client_name_with_amazon(self):
        self.service_description['metadata']['serviceFullName'] = (
            'Amazon MyService')
        self.assertEqual(
            get_service_module_name(self.service_model),
            'MyService'
        )

    def test_client_name_using_abreviation(self):
        self.service_description['metadata']['serviceAbbreviation'] = (
            'Abbreviation')
        self.assertEqual(
            get_service_module_name(self.service_model),
            'Abbreviation'
        )

    def test_client_name_with_non_alphabet_characters(self):
        self.service_description['metadata']['serviceFullName'] = (
            'Amazon My-Service')
        self.assertEqual(
            get_service_module_name(self.service_model),
            'MyService'
        )

    def test_client_name_with_no_full_name_or_abbreviation(self):
        del self.service_description['metadata']['serviceFullName']
        self.assertEqual(
            get_service_module_name(self.service_model),
            'myservice'
        )


class TestPercentEncodeSequence(unittest.TestCase):
    def test_percent_encode_empty(self):
        self.assertEqual(percent_encode_sequence({}), '')

    def test_percent_encode_special_chars(self):
        self.assertEqual(
            percent_encode_sequence({'k1': 'with spaces++/'}), 'k1=with%20spaces%2B%2B%2F')

    def test_percent_encode_string_string_tuples(self):
        self.assertEqual(percent_encode_sequence([('k1', 'v1'), ('k2', 'v2')]),
                         'k1=v1&k2=v2')

    def test_percent_encode_dict_single_pair(self):
        self.assertEqual(percent_encode_sequence({'k1': 'v1'}), 'k1=v1')

    def test_percent_encode_dict_string_string(self):
        self.assertEqual(
            percent_encode_sequence(OrderedDict([('k1', 'v1'), ('k2', 'v2')])),
                                    'k1=v1&k2=v2')

    def test_percent_encode_single_list_of_values(self):
        self.assertEqual(percent_encode_sequence({'k1': ['a', 'b', 'c']}),
                         'k1=a&k1=b&k1=c')

    def test_percent_encode_list_values_of_string(self):
        self.assertEqual(
            percent_encode_sequence(
                OrderedDict([('k1', ['a', 'list']), ('k2', ['another', 'list'])])),
            'k1=a&k1=list&k2=another&k2=list')


if __name__ == '__main__':
    unittest.main()
