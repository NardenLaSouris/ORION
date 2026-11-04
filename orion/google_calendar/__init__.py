"""Agenda Google (V3) : le calendrier Google « ORION », lu par son adresse secrète iCal, en lecture seule
(docs/v3/google_calendar.md)."""

from orion.google_calendar.module import (GoogleCalendarModule, GoogleCalendarProvider, GoogleCalendarSettings,
                                          IcalError, fetch_ical)

__all__ = ["GoogleCalendarModule", "GoogleCalendarProvider", "GoogleCalendarSettings", "IcalError", "fetch_ical"]
