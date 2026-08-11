from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Liveness plus a database round trip."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return Response({"status": "ok", "database": "ok"})


@api_view(["GET"])
def me(request):
    """Who am I, and which organization am I acting for."""
    user = request.user
    org = user.organization
    return Response(
        {
            "id": str(user.id),
            "username": user.username,
            "name": user.get_full_name() or user.username,
            "organization": (
                {
                    "id": str(org.id),
                    "name": org.name,
                    "primary_kind": org.primary_kind,
                }
                if org
                else None
            ),
        }
    )
