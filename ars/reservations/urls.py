from __future__ import annotations

from django.urls import path

from reservations import views

urlpatterns = [
    path('', views.flight_list, name='flight-list'),
    path('flights/<int:flight_id>/', views.flight_detail, name='flight-detail'),
    path('flights/<int:flight_id>/map/', views.seat_map, name='seat-map'),
    path('flights/<int:flight_id>/book/', views.book_seat, name='book-seat'),
    path('flights/<int:flight_id>/book/first/', views.book_first, name='book-first'),
]
