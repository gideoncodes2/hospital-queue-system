from django.core.management.base import BaseCommand

from clinic import services


class Command(BaseCommand):
    help = "Send 'turn is near' and delay SMS alerts. Schedule this to run every minute."

    def handle(self, *args, **options):
        self.stdout.write(f"{services.process_alerts()} alert(s) sent.")
