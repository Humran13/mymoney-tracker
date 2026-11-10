from django.urls import path

from . import views

app_name = "feedback"

urlpatterns = [
    path("", views.feedback_list, name="list"),
    path("new/", views.feedback_create, name="create"),
    path("<int:pk>/", views.feedback_detail, name="detail"),
    path("<int:pk>/attachment/", views.feedback_attachment, name="attachment"),
]
