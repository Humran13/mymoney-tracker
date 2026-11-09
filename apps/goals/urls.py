from django.urls import path

from . import views

app_name = "goals"

urlpatterns = [
    path("", views.goal_list, name="list"),
    path("new/", views.goal_create, name="create"),
    path("<int:pk>/", views.goal_detail, name="detail"),
    path("<int:pk>/edit/", views.goal_update, name="update"),
    path("<int:pk>/pause/", views.goal_pause, name="pause"),
    path("<int:pk>/archive/", views.goal_archive, name="archive"),
    path("required-income/", views.required_income, name="required_income"),
]
