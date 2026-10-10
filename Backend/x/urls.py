from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from api.views import AuthRateThrottle
from api.mature import MatureTokenObtainPairView, MatureTokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),

    path(
        'api/token/',
        MatureTokenObtainPairView.as_view(throttle_classes=[AuthRateThrottle]),
        name='token_obtain_pair'
    ),

    path(
        'api/token/refresh/',
        MatureTokenRefreshView.as_view(),
        name='token_refresh'
    ),
]

# Profile photos are public identity assets. Render does not automatically serve
# MEDIA_URL, so explicitly serve only the avatar subtree in every environment.
# Keep other user-uploaded media private and outside this route.
urlpatterns += [
    re_path(
        r"^media/avatars/(?P<path>.*)$",
        serve,
        {"document_root": settings.MEDIA_ROOT / "avatars"},
        name="profile-avatar-media",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
