"""Sync and telemetry endpoints — what the site agent talks to.

The agent is the least trusted input in the system: it runs on a machine
in a pharmacy, offline for hours at a time, replaying whatever it
buffered. So every envelope is whitelisted by kind, applied through the
ordinary service layer, and answered idempotently.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from rest_framework import serializers, status, viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from core.permissions import TenantScoped
from rest_framework.response import Response
from rest_framework.views import APIView

from core import sync
from core.exceptions import DomainError
from core.sync import Device, SyncEnvelope, SyncStatus
from inventory.telemetry import Excursion, Sensor, record_reading, resolve_excursion


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = [
            "id",
            "code",
            "name",
            "branch",
            "location",
            "is_active",
            "last_seen_at",
            "agent_version",
        ]


class SensorSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = Sensor
        fields = [
            "id",
            "location",
            "location_name",
            "device_code",
            "name",
            "minimum_c",
            "maximum_c",
            "is_active",
            "last_seen_at",
        ]


class ExcursionSerializer(serializers.ModelSerializer):
    sensor_name = serializers.CharField(source="sensor.__str__", read_only=True)
    location_name = serializers.CharField(source="sensor.location.name", read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Excursion
        fields = [
            "id",
            "sensor",
            "sensor_name",
            "location_name",
            "started_at",
            "ended_at",
            "is_open",
            "duration_minutes",
            "peak_celsius",
            "minimum_celsius",
            "reading_count",
            "quarantined_base",
            "batches_affected",
            "resolved_at",
            "resolution",
        ]


class EnvelopeSerializer(serializers.Serializer):
    """One journalled operation from the agent."""

    client_id = serializers.CharField(max_length=64)
    kind = serializers.CharField(max_length=40)
    payload = serializers.JSONField()
    occurred_at = serializers.DateTimeField()
    sequence = serializers.IntegerField(default=0)


class SyncBatchSerializer(serializers.Serializer):
    """A whole replay, in one round trip.

    Batched because the interesting case is a reconnection after hours
    offline, and one request per journalled sale would turn that into a
    thundering herd from every pharmacy at once when the network returns.
    """

    device = serializers.CharField(max_length=60)
    agent_version = serializers.CharField(max_length=20, required=False, allow_blank=True)
    envelopes = EnvelopeSerializer(many=True)


class SyncView(APIView):
    """Replay what the agent buffered.

    Answers per envelope so a partial failure is visible: the agent
    clears what applied and keeps what did not, rather than deciding the
    whole batch failed because one payload was bad.
    """

    permission_classes = [IsAuthenticated, TenantScoped]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sync"

    def post(self, request):
        payload = SyncBatchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        device = Device.objects.filter(
            organization=request.user.organization, code=data["device"], is_active=True
        ).first()
        if device is None:
            # Refused rather than created: a typo'd code would otherwise
            # look like a new till whose sequence starts from nothing.
            raise DomainError(
                f"Unknown device '{data['device']}'.", code="unknown_device"
            )

        if data.get("agent_version"):
            device.agent_version = data["agent_version"]
            device.save(update_fields=["agent_version", "modified_at"])

        results = []
        for entry in data["envelopes"]:
            applied = sync.apply_envelope(
                organization=request.user.organization,
                device=device,
                client_id=entry["client_id"],
                kind=entry["kind"],
                payload=entry["payload"],
                occurred_at=entry["occurred_at"],
                sequence=entry.get("sequence", 0),
                performed_by=request.user,
            )
            results.append(
                {
                    "client_id": entry["client_id"],
                    "status": (
                        SyncStatus.DUPLICATE
                        if applied.replayed
                        else applied.envelope.status
                    ),
                    "result": applied.envelope.result,
                    "error": applied.envelope.error,
                }
            )

        return Response({"accepted": len(results), "results": results})


class TelemetryView(APIView):
    """Sensor readings, live or buffered.

    Separate from `/sync/` because temperature is the one thing worth
    sending the moment it is taken: an excursion caught in ten minutes is
    a fridge somebody can still save, and one caught at the next
    reconnection is a write-off.
    """

    permission_classes = [IsAuthenticated, TenantScoped]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sync"

    def post(self, request):
        sensor = Sensor.objects.filter(
            organization=request.user.organization,
            device_code=request.data.get("sensor", ""),
            is_active=True,
        ).first()
        if sensor is None:
            raise DomainError(
                f"Unknown sensor '{request.data.get('sensor', '')}'.",
                code="unknown_sensor",
            )

        stored = 0
        duplicates = 0
        opened = []
        for entry in request.data.get("readings", []):
            result = record_reading(
                sensor=sensor,
                celsius=entry["celsius"],
                recorded_at=datetime.fromisoformat(entry["at"]),
                was_buffered=bool(entry.get("buffered")),
                performed_by=request.user,
            )
            if result.duplicate:
                duplicates += 1
                continue
            stored += 1
            if result.opened:
                opened.append(
                    {
                        "excursion": str(result.excursion.id),
                        "quarantined_base": result.quarantined_base,
                    }
                )

        return Response(
            {"stored": stored, "duplicates": duplicates, "excursions_opened": opened},
            status=status.HTTP_201_CREATED,
        )


class DeviceViewSet(viewsets.ModelViewSet):
    serializer_class = DeviceSerializer

    def get_queryset(self):
        return Device.tenant_objects.select_related("branch", "location")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class SensorViewSet(viewsets.ModelViewSet):
    serializer_class = SensorSerializer

    def get_queryset(self):
        return Sensor.tenant_objects.select_related("location")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class ExcursionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only. An excursion is a record of what happened.

    The one action is resolving it — saying what was decided about the
    stock — which is not the same as editing the event.
    """

    serializer_class = ExcursionSerializer

    def get_queryset(self):
        queryset = Excursion.tenant_objects.select_related("sensor__location")
        if self.request.query_params.get("open") == "true":
            queryset = queryset.filter(ended_at__isnull=True)
        return queryset


class ResolveExcursionView(APIView):
    permission_classes = [IsAuthenticated, TenantScoped]

    def post(self, request, excursion_id):
        from rest_framework.generics import get_object_or_404

        excursion = resolve_excursion(
            excursion=get_object_or_404(Excursion.tenant_objects, pk=excursion_id),
            performed_by=request.user,
            resolution=request.data.get("resolution", ""),
        )
        return Response(ExcursionSerializer(excursion).data)


class SyncStatusView(APIView):
    """What the agents have been doing, and what failed.

    A failed envelope is kept rather than dropped — it is evidence of a
    bug, and it is the only copy of whatever the pharmacy recorded.
    """

    permission_classes = [IsAuthenticated, TenantScoped]

    def get(self, request):
        organization = request.user.organization
        devices = Device.objects.filter(organization=organization)
        failures = SyncEnvelope.objects.filter(
            organization=organization, status=SyncStatus.FAILED
        ).select_related("device")[:50]

        return Response(
            {
                "devices": [
                    {
                        "code": device.code,
                        "name": device.name,
                        "last_seen_at": device.last_seen_at,
                        "agent_version": device.agent_version,
                        "is_active": device.is_active,
                        "silent_hours": (
                            int(
                                (timezone.now() - device.last_seen_at).total_seconds()
                                / 3600
                            )
                            if device.last_seen_at
                            else None
                        ),
                    }
                    for device in devices
                ],
                "failures": [
                    {
                        "client_id": row.client_id,
                        "device": row.device.code,
                        "kind": row.kind,
                        "error": row.error,
                        "occurred_at": row.occurred_at,
                    }
                    for row in failures
                ],
            }
        )
