from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

MAX_DAYS_AHEAD = 7  # forecasts get unreliable beyond this; Open-Meteo stops at 16 days


class PlanRequestSerializer(serializers.Serializer):
    origin = serializers.CharField(max_length=200)
    destination = serializers.CharField(max_length=200)
    departure = serializers.DateTimeField()
    load_lb = serializers.FloatField(min_value=0, max_value=200_000)
    interval_mi = serializers.ChoiceField(choices=[10, 25, 50], default=25)

    def validate_departure(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=1):
            raise serializers.ValidationError('Departure cannot be in the past.')
        if value > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(f'Departure must be within {MAX_DAYS_AHEAD} days.')
        return value

    def validate(self, attrs):
        if attrs['origin'].strip().lower() == attrs['destination'].strip().lower():
            raise serializers.ValidationError('Origin and destination must be different.')
        return attrs
