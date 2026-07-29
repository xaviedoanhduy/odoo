import datetime
import logging
import pytz
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase
from odoo.tools import date_utils
from odoo._monkeypatches.pytz import _tz_mapping

_logger = logging.getLogger(__name__)


class TestTZ(TransactionCase):

    def test_tz_legacy(self):
        d = datetime.datetime(1969, 7, 16)
        # See https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
        def assertTZEqual(tz1, tz2):
            self.assertEqual(tz1.localize(d).strftime('%z'), tz2.localize(d).strftime('%z'))

            # in some version of tzdata the timezones are not symlink, as an example in 2023c-0ubuntu0.20.04.1
            # this as a side effect to have sligh difference in timezones seconds, breaking the following assertions
            # in some cases:
            #
            # self.assertEqual(tz1._utcoffset, tz2._utcoffset)
            # if hasattr(tz2, '_transition_info'):
            #     self.assertEqual(tz1._transition_info, tz2._transition_info)
            #
            # the first one is more robust

        for source, target in _tz_mapping.items():
            with self.subTest(source=source, target=target):
                if source == 'Pacific/Enderbury':  # this one was wrong in some version of tzdata
                    continue
                try:
                    target_tz = pytz.timezone(target)
                except pytz.UnknownTimeZoneError:
                    _logger.info("Skipping test for %s -> %s, target does not exist", source, target)
                    continue
                assertTZEqual(pytz.timezone(source), target_tz)

    def test_dont_adapt_available_tz(self):
        with patch.dict(_tz_mapping, {
            'DeprecatedUtc': 'UTC',
            'America/New_York': 'UTC',
        }):
            self.assertNotIn('DeprecatedUtc', pytz.all_timezones_set, 'DeprecatedUtc is not available')
            self.assertEqual(pytz.timezone('DeprecatedUtc'), pytz.timezone('UTC'), 'DeprecatedUtc does not exist and should have been replaced with UTC')
            self.assertIn('America/New_York', pytz.all_timezones_set, 'America/New_York is available')
            self.assertNotEqual(pytz.timezone('America/New_York'), pytz.timezone('UTC'), 'America/New_York exists and should not have been replaced with UTC')

    def test_cannot_set_deprecated_timezone(self):
        # this should be ok
        self.env.user.tz = "America/New_York"
        if "US/Eastern" not in pytz.all_timezones:
            with self.assertRaises(ValueError):
                self.env.user.tz = "US/Eastern"

    def test_partner_with_old_tz(self):
        # this test makes sence after ubuntu noble without tzdata-legacy installed
        partner = self.env['res.partner'].create({'name': 'test', 'tz': 'UTC'})
        self.env.cr.execute("""UPDATE res_partner set tz='US/Eastern' WHERE id=%s""", (partner.id,))
        partner.invalidate_recordset()
        self.assertEqual(partner.tz, 'US/Eastern')  # tz was update despite selection not existing, but data was not migrated
        # comparing with 'America/New_York' see tools/_monkeypatches_pytz.py for mapping
        expected_offset = datetime.datetime.now(pytz.timezone('America/New_York')).strftime('%z')
        # offest will be -0400 in summer, -0500 in winter
        self.assertEqual(partner.tz_offset, expected_offset, "We don't expect pytz.timezone to fail if the timezone diseapeared when chaging os version")

    def test_canonical_timezone(self):
        self.assertEqual(date_utils.canonical_timezone('Asia/Saigon'), 'Asia/Ho_Chi_Minh')
        # values without a known equivalent are returned unchanged
        self.assertEqual(date_utils.canonical_timezone('Europe/Brussels'), 'Europe/Brussels')
        self.assertEqual(date_utils.canonical_timezone(False), False)
        # a mapping is not applied when the target is missing from the system
        with patch.dict(_tz_mapping, {'Old/Zone': 'Nowhere/Unknown'}):
            self.assertEqual(date_utils.canonical_timezone('Old/Zone'), 'Old/Zone')

    def test_read_group_deprecated_timezone(self):
        # the sql guard used to skip the conversion for a deprecated timezone,
        # grouping in UTC instead of the timezone of the user
        self.env['res.partner'].create({'name': 'tz test'})
        domain = [('name', '=', 'tz test')]
        self.assertEqual(
            self.env['res.partner'].with_context(tz='Asia/Saigon')._read_group(domain, ['create_date:day']),
            self.env['res.partner'].with_context(tz='Asia/Ho_Chi_Minh')._read_group(domain, ['create_date:day']),
        )

    def test_login_deprecated_timezone(self):
        # browsers report the CLDR name, which is deprecated for some timezones
        user = self.env['res.users'].create({
            'name': 'tz test',
            'login': 'tz_test',
            'password': 'tz_test',
            'tz': False,
        })
        request = MagicMock(cookies={'tz': 'Asia/Saigon'})
        with patch('odoo.addons.base.models.res_users.request', request):
            self.env['res.users']._login(
                {'login': 'tz_test', 'password': 'tz_test', 'type': 'password'},
                {'interactive': False},
            )
        user.invalidate_recordset()
        self.assertEqual(user.tz, 'Asia/Ho_Chi_Minh')
