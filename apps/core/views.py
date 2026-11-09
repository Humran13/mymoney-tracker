from django.db import connection
from django.http import JsonResponse


def health_check(request):
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "error", "database": db_ok}, status=status)
