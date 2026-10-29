from django.core.management.base import BaseCommand

from clinic import services


class Command(BaseCommand):
    help = "Send day-before reminders for tomorrow's booked appointments. Schedule this once a day."

    def handle(self, *args, **options):
        self.stdout.write(f"{services.send_reminders()} reminder(s) sent.")
