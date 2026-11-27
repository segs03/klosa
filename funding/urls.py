from django.urls import path

from funding import views, views_contrib

app_name = "funding"

urlpatterns = [
    path("periods/", views.period_list_view, name="periods"),
    path("periods/open/", views.period_open_view, name="period_open"),
     path("periods/<int:period_id>/", views.period_detail_view, name="period"),
    path("periods/<int:period_id>/review/", views.period_review_view, name="review_period"),
    path("periods/<int:period_id>/close/", views.period_close_view, name="close_period"),
    path("periods/<int:period_id>/reopen/", views.reopen_period_view, name="reopen_period"),
    path("periods/<int:period_id>/allocations/", views.period_allocations_view, name="allocations"),

    path("my-deposits/", views_contrib.my_deposits, name="my_deposits"),
    path("intents/", views_contrib.intents_queue, name="intents"),
    path("contributions/", views_contrib.contributions_queue, name="contributions"),
]


