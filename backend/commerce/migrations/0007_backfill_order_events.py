"""Reconstruct the timeline for orders raised before it existed.

An order that already shipped should not open with a blank history. The
timestamps already on the row — submitted, confirmed — are what we can
honestly reconstruct, so they become events and nothing is invented.

The actor is left null on purpose. `approved_by` records the last person
to approve, which is not reliably the person who submitted, and guessing
would put a name against an action someone may not have taken.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    PurchaseOrder = apps.get_model("commerce", "PurchaseOrder")
    OrderEvent = apps.get_model("commerce", "OrderEvent")

    events = []
    for order in PurchaseOrder.objects.all().iterator():
        if order.submitted_at:
            events.append(
                OrderEvent(
                    order=order,
                    from_status="PENDING_APPROVAL",
                    to_status="SUBMITTED",
                    occurred_at=order.submitted_at,
                    document_number=order.number,
                    note="Reconstructed from the order record.",
                )
            )
        if order.confirmed_at:
            events.append(
                OrderEvent(
                    order=order,
                    from_status="SUBMITTED",
                    to_status="CONFIRMED",
                    occurred_at=order.confirmed_at,
                    note="Reconstructed from the order record.",
                )
            )
        # Where the order has moved past confirmation, close the timeline
        # with its current state so the last row is not misleading.
        if order.status not in ("DRAFT", "PENDING_APPROVAL", "SUBMITTED", "CONFIRMED"):
            events.append(
                OrderEvent(
                    order=order,
                    from_status="CONFIRMED",
                    to_status=order.status,
                    occurred_at=order.modified_at,
                    note="Reconstructed from the order record.",
                )
            )

    OrderEvent.objects.bulk_create(events, batch_size=500)


def unbackfill(apps, schema_editor):
    OrderEvent = apps.get_model("commerce", "OrderEvent")
    OrderEvent.objects.filter(note="Reconstructed from the order record.").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0006_shipment_driver_licence_shipment_driver_name_and_more"),
    ]

    operations = [migrations.RunPython(backfill, unbackfill)]
