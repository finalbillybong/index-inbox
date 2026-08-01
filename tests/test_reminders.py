import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reminders import parse_reminder


class ReminderParserTests(unittest.TestCase):
    reference=datetime(2026,1,15,12,0,tzinfo=timezone.utc)  # Thursday

    def assert_reminder(self,phrase,text,due,**extra):
        self.assertEqual(parse_reminder(phrase,self.reference,timezone.utc,"24"),{"text":text,"due_at":due,**extra})

    def test_relative_and_compound_durations(self):
        self.assert_reminder("Remind me in half an hour to check the oven","check the oven","2026-01-15T12:30:00+00:00")
        self.assert_reminder("Remind me in two hours and thirty minutes to call Mum","call Mum","2026-01-15T14:30:00+00:00")
        self.assert_reminder("Don't forget in a couple of weeks to renew the filter","renew the filter","2026-01-29T12:00:00+00:00")

    def test_calendar_periods_use_calendar_arithmetic(self):
        month_end=datetime(2026,1,31,10,0,tzinfo=timezone.utc)
        result=parse_reminder("Remind me in one month to renew",month_end)
        self.assertEqual(result["due_at"],"2026-02-28T10:00:00+00:00")

    def test_day_parts_and_explicit_overrides(self):
        self.assert_reminder("Remind me tomorrow morning to stretch","stretch","2026-01-16T09:00:00+00:00")
        self.assert_reminder("Remind me tomorrow evening to call home","call home","2026-01-16T19:00:00+00:00")
        self.assert_reminder("Remind me tomorrow morning at 7:45 a.m. to grab earbuds","grab earbuds","2026-01-16T07:45:00+00:00")
        self.assert_reminder("Remind me at 8 tomorrow morning to stretch","stretch","2026-01-16T08:00:00+00:00")

    def test_weekdays_in_both_orders(self):
        self.assert_reminder("Remind me next Monday at 9am to submit expenses","submit expenses","2026-01-19T09:00:00+00:00")
        self.assert_reminder("Remind me at 3pm next Friday to call Mum","call Mum","2026-01-16T15:00:00+00:00")
        self.assert_reminder("Remind me Saturday to wash the car","wash the car","2026-01-17T09:00:00+00:00")

    def test_named_dates_ordinals_and_iso_dates(self):
        self.assert_reminder("Remind me on August twenty-fourth, 2026 at 14:30 to renew insurance","renew insurance","2026-08-24T14:30:00+00:00")
        self.assert_reminder("Remind me on 2026-08-04 at 14.30 to renew certificate","renew certificate","2026-08-04T14:30:00+00:00")
        self.assert_reminder("Remind me on 8/24/2026 at 14:30 to renew insurance","renew insurance","2026-08-24T14:30:00+00:00")

    def test_weekend_and_next_week_defaults(self):
        self.assert_reminder("Remind me this weekend to wash the car","wash the car","2026-01-17T09:00:00+00:00")
        self.assert_reminder("Remind me next week to review the budget","review the budget","2026-01-19T09:00:00+00:00")

    def test_bare_times_and_clock_format_policy(self):
        self.assert_reminder("Remind me at 7.30 to have coffee","have coffee","2026-01-16T07:30:00+00:00")
        twelve=parse_reminder("Remind me at 7.30 to have coffee",self.reference,timezone.utc,"12")
        self.assertEqual(twelve["due_at"],"2026-01-15T19:30:00+00:00")

    def test_lead_time_is_extracted(self):
        self.assert_reminder(
            "Remind me to call the dentist tomorrow at 3pm with one hour notice",
            "call the dentist","2026-01-16T15:00:00+00:00",notify_before_minutes=60,
        )

    def test_original_timezone_is_applied(self):
        london=ZoneInfo("Europe/London")
        summer=datetime(2026,7,30,12,0,tzinfo=timezone.utc)
        result=parse_reminder("Remind me tomorrow at 9am to call Mum",summer,london,"24")
        self.assertEqual(result["due_at"],"2026-07-31T08:00:00+00:00")

    def test_explicit_past_and_recurrence_are_rejected(self):
        self.assertIsNone(parse_reminder("Remind me today at 9am to call Mum",self.reference))
        self.assertIsNone(parse_reminder("Remind me every Monday at 9am to call Mum",self.reference))
        self.assertIsNone(parse_reminder("Remind me last weekend to check the cabin",self.reference))

    def test_non_reminder_text_is_not_reclassified(self):
        self.assertIsNone(parse_reminder("Perhaps remind me about this sometime",self.reference))
        self.assertIsNone(parse_reminder("Meeting tomorrow at 3pm",self.reference))


if __name__=="__main__":unittest.main()
