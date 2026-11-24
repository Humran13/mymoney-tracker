from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("", views.hub, name="hub"),
    path("held/new/", views.fund_create, name="fund_create"),
    path("held/<int:pk>/", views.fund_detail, name="fund_detail"),
    path("held/<int:pk>/edit/", views.fund_edit, name="fund_edit"),
    path("held/<int:pk>/movement/new/", views.fund_entry, name="fund_entry"),
    path("held/<int:pk>/movement/<int:entry_pk>/edit/", views.fund_entry, name="fund_entry_edit"),
    path("reimbursements/new/", views.reimbursement_create, name="reimbursement_create"),
    path("reimbursements/<int:pk>/", views.reimbursement_detail, name="reimbursement_detail"),
    path("reimbursements/<int:pk>/edit/", views.reimbursement_edit, name="reimbursement_edit"),
    path("reimbursements/<int:pk>/payment/new/", views.reimbursement_payment, name="reimbursement_payment"),
    path("reimbursements/<int:pk>/payment/<int:payment_pk>/edit/", views.reimbursement_payment, name="reimbursement_payment_edit"),
    path("inventory/new/", views.lot_create, name="lot_create"),
    path("inventory/<int:pk>/", views.lot_detail, name="lot_detail"),
    path("inventory/<int:pk>/edit/", views.lot_edit, name="lot_edit"),
    path("inventory/<int:pk>/sale/new/", views.sale_create, name="sale_create"),
    path("sales/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("sales/<int:pk>/edit/", views.sale_edit, name="sale_edit"),
    path("sales/<int:pk>/payment/new/", views.sale_payment, name="sale_payment"),
    path("sales/<int:pk>/payment/<int:payment_pk>/edit/", views.sale_payment, name="sale_payment_edit"),
    path("reverse/<str:kind>/<int:pk>/", views.reverse_record, name="reverse"),
]
