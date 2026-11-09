from .models import AuditEvent


def log_audit_event(
    actor,
    action,
    *,
    affected_user=None,
    obj=None,
    description="",
    metadata=None,
    request=None,
):
    ip_address = None
    if request is not None:
        ip_address = request.META.get("REMOTE_ADDR")

    AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        affected_user=affected_user,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        description=description,
        metadata=metadata or {},
        ip_address=ip_address,
    )
