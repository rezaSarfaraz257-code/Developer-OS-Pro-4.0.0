from django.urls import re_path
from .ide_consumer import IDETerminalConsumer
websocket_urlpatterns = [
    re_path(r"^ws/ide/workspaces/(?P<workspace_id>\\d+)/terminal/$", IDETerminalConsumer.as_asgi()),
]
