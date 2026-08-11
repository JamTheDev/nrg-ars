from __future__ import annotations

from django.urls import path

from reservations import views

urlpatterns = [
    path('', views.flight_list, name='flight-list'),
    path('flights/<int:flight_id>/', views.flight_detail, name='flight-detail'),
]
