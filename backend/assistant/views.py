"""Two endpoints: ask, and decide.

The rate limit is the one in docs/07 — 30/min per user. It is lower than
the standard 600 because a question fans out into aggregate queries that
a normal list endpoint does not, and because a client looping on a
question box is the shape a runaway takes here.
"""

from __future__ import annotations

from rest_framework import serializers, viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from assistant import services
from assistant.models import Proposal
from core.permissions import TenantScoped


class AssistantThrottle(UserRateThrottle):
    scope = "assistant"


class ProposalSerializer(serializers.ModelSerializer):
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Proposal
        fields = [
            "id",
            "question",
            "action",
            "effect",
            "status",
            "result",
            "error",
            "expires_at",
            "decided_at",
            "is_open",
            "created_at",
        ]


class AskView(APIView):
    """Answer a question. Reads only — see assistant/services.py."""

    permission_classes = [IsAuthenticated, TenantScoped]
    throttle_classes = [AssistantThrottle]

    def post(self, request):
        asked = services.ask(user=request.user, question=request.data.get("question", ""))
        return Response(asked.answer.as_dict())


class ConfirmView(APIView):
    """Carry out a proposal, or record that it was declined."""

    permission_classes = [IsAuthenticated, TenantScoped]
    throttle_classes = [AssistantThrottle]

    def post(self, request, proposal_id):
        from rest_framework.generics import get_object_or_404

        proposal = services.confirm(
            user=request.user,
            proposal=get_object_or_404(Proposal.tenant_objects, pk=proposal_id),
            # Absent means declined. A confirmation that defaults to yes
            # is not a confirmation.
            accepted=bool(request.data.get("accepted")),
            reason=request.data.get("reason", ""),
        )
        return Response(ProposalSerializer(proposal).data)


class ProposalViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """What was suggested, and what was decided about it.

    Read-only: a proposal is changed by confirming or declining it, which
    is a different endpoint with a different record.
    """

    serializer_class = ProposalSerializer

    def get_queryset(self):
        queryset = Proposal.tenant_objects.all()
        status = self.request.query_params.get("status")
        return queryset.filter(status=status) if status else queryset
