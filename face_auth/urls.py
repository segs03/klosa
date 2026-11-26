from django.urls import path
from . import views

app_name = "face_auth"


urlpatterns = [
    path("begin/", views.begin, name="begin"),
    path("begin/<str:purpose>/", views.begin, name="begin"),
    path("capture/", views.capture, name="capture"),
    path("frame/", views.frame, name="frame"),
    path("result/", views.result, name="result"),
    path("staff/", views.queue, name="queue"),
    path("staff/<int:pk>/", views.review, name="review"),
    path("staff/<int:pk>/<int:step>/image/", views.image, name="image"),
]