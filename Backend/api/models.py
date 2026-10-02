from uuid import uuid4

from django.contrib.auth.models import User
from django.db import models
from django.db.models.functions import Lower
from django.utils.text import slugify
from django.utils import timezone

from .fields import EncryptedTextField

def profile_avatar_upload_to(instance, _filename):
    """Store avatars under an opaque, user-scoped name.

    The API re-encodes every accepted avatar as WebP before saving it, so the
    generated extension is deliberate rather than supplied by the client.
    """
    return f"avatars/user_{instance.user_id}/{uuid4().hex}.webp"


# Create your models here.

class Project(models.Model):
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects"
    )

    CATEGORY_CHOICES = [
        ("Frontend", "Frontend"),
        ("Backend", "Backend"),
        ("DevOps", "DevOps"),
        ("AI", "AI"),
        ("Design", "Design"),
        ("Productivity", "Productivity"),
        ("General", "General"),
    ]

    STATUS_CHOICES = [
        ("In Progress", "In Progress"),
        ("Planning", "Planning"),
        ("Completed", "Completed"),
        ("On Hold", "On Hold"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default="General"
    )
    tags = models.JSONField(default=list, blank=True)
    link = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        default="In Progress",
    )
    PRIORITY_CHOICES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")]
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default="medium")
    start_date = models.DateField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    repository_url = models.URLField(blank=True, default="")
    stack = models.JSONField(default=list, blank=True)
    collaborators = models.ManyToManyField(User, related_name="collaborated_projects", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=200, blank=True, default="")
    avatar = models.ImageField(upload_to=profile_avatar_upload_to, blank=True)
    avatar_url = models.URLField(blank=True, default="")
    bio = models.TextField(blank=True, default="")
    github = models.URLField(blank=True, default="")
    linkedin = models.URLField(blank=True, default="")
    x = models.URLField(blank=True, default="")
    website = models.URLField(blank=True, default="")
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} profile"

class Resource(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    resource_type = models.CharField(max_length=50, default="Guide")
    category = models.CharField(max_length=80, default="General")
    link = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class Workflow(models.Model):
    title = models.CharField(max_length=200)
    level = models.CharField(max_length=50, default="Beginner")
    duration = models.CharField(max_length=50, default="Flexible")
    summary = models.TextField(blank=True, default="")
    steps = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class Tool(models.Model):
    CATEGORY_CHOICES = [
        ("Frontend", "Frontend"),
        ("Backend", "Backend"),
        ("DevOps", "DevOps"),
        ("AI", "AI"),
        ("Design", "Design"),
        ("Productivity", "Productivity"),
        ("General", "General"),
    ]

    name = models.CharField(max_length=120)
    tag = models.CharField(max_length=50, default="General")
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="General")
    description = models.TextField(blank=True, default="")
    accent = models.CharField(max_length=20, default="cyan")
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.5)
    features = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="unique_tool_name_case_insensitive",
            )
        ]

class Favorite(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="favorites"
    )

    tool = models.ForeignKey(
        Tool,
        on_delete=models.CASCADE,
        related_name="favorited_by",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tool"],
                name="unique_user_tool_favorite"
            )
        ]

    def __str__(self):
        return f"{self.user.username}: {self.tool.name}"

class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:90] or "tag"
            candidate = base_slug
            suffix = 2
            while type(self).objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                candidate = f"{base_slug[:95 - len(str(suffix))]}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

class Task(models.Model):
    STATUS = [
        ("todo", "To Do"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("blocked", "Blocked"),
    ]

    PRIORITY = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tasks",
        null=True,
        blank=True,
    )

    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )

    status = models.CharField(
        max_length=32,
        choices=STATUS,
        default="todo",
    )

    priority = models.CharField(
        max_length=16,
        choices=PRIORITY,
        default="medium",
    )

    title = models.CharField(
        max_length=200
    )

    description = models.TextField(
        blank=True,
        default=""
    )

    due_date = models.DateField(
        null=True,
        blank=True
    )

    tags = models.JSONField(
        default=list,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return self.title

class Note(models.Model):
    title = models.CharField(max_length=200, blank=True, default="")
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="notes")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name="notes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or f"Note {self.pk}"

class Activity(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities")
    verb = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    related_type = models.CharField(max_length=100, blank=True, default="")
    related_id = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.actor or 'System'} {self.verb}"

class Snippet(models.Model):
    LANGUAGE_CHOICES = [
        ("py", "Python"),
        ("js", "JavaScript"),
        ("sh", "Shell"),
        ("sql", "SQL"),
        ("md", "Markdown"),
        ("txt", "Text"),
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="snippets",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=200)
    code = models.TextField()
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default="py")
    description = models.TextField(blank=True, default="")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="snippets")
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

class GitHubOAuthState(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="github_states")
    state = models.CharField(max_length=200, unique=True)
    code_verifier = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {self.state}"

class GitHubAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="github_account")
    github_id = models.IntegerField(null=True, blank=True)
    login = models.CharField(max_length=200, blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    scope = models.CharField(max_length=200, blank=True, default="")
    token_type = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} -> {self.login or 'github'}"



# =========================================================
# DEVELOPER OS 2.0 — Intelligence / Collaboration / SaaS / IDE
# =========================================================

class Organization(models.Model):
    PLAN_CHOICES = [("free", "Free"), ("pro", "Pro"), ("team", "Team"), ("enterprise", "Enterprise")]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="owned_organizations")
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    provider_customer_id = models.CharField(max_length=180, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    ROLE_CHOICES = [("owner", "Owner"), ("admin", "Admin"), ("developer", "Developer"), ("viewer", "Viewer")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="developer")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="unique_org_member")]


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=40, default="system")
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True, default="")
    link = models.CharField(max_length=500, blank=True, default="")
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "read", "-created_at"], name="api_notif_user_read_created"),
            models.Index(fields=["user", "-created_at"], name="api_notif_user_created"),
        ]


class Comment(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="comments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments", null=True, blank=True)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments", null=True, blank=True)
    body = models.TextField(max_length=10000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class TaskDependency(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="dependencies")
    depends_on = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="dependents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "depends_on"], name="unique_task_dependency"),
        ]


class ProjectInvite(models.Model):
    ROLE_CHOICES = [("admin", "Admin"), ("developer", "Developer"), ("viewer", "Viewer")]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="invites")
    inviter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_project_invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="developer")
    token = models.CharField(max_length=96, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CodeWorkspace(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="code_workspaces")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="code_workspaces", null=True, blank=True)
    name = models.CharField(max_length=160, default="Untitled Workspace")
    files = models.JSONField(default=dict, blank=True)
    active_file = models.CharField(max_length=500, default="main.py")
    language = models.CharField(max_length=40, default="python")
    framework = models.CharField(max_length=80, blank=True, default="")
    runtime = models.CharField(max_length=40, blank=True, default="python")
    package_manager = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    revision = models.PositiveBigIntegerField(default=1)

    def save(self, *args, **kwargs):
        # Every persisted IDE mutation gets a monotonic revision so clients can
        # detect stale editors and avoid silently losing newer work.
        if self.pk and not self._state.adding:
            self.revision = (self.revision or 0) + 1
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"revision", "updated_at"}
        super().save(*args, **kwargs)

    class Meta:
        indexes = [models.Index(fields=["owner", "-updated_at"], name="api_codework_owner_updated")]

class FrameworkInstallation(models.Model):
    STATUS_CHOICES = [("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("failed", "Failed")]
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name="installations")
    framework = models.CharField(max_length=80)
    package_manager = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    output = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

class IDEExecution(models.Model):
    STATUS_CHOICES = [("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("timeout", "Timeout")]
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name="executions")
    command = models.CharField(max_length=2000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    exit_code = models.IntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class AIConversation(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ai_conversations")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="ai_conversations", null=True, blank=True)
    title = models.CharField(max_length=200, default="New conversation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AIMessage(models.Model):
    ROLE_CHOICES = [("system", "System"), ("user", "User"), ("assistant", "Assistant"), ("tool", "Tool")]
    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Subscription(models.Model):
    PLAN_CHOICES = [("free", "Free"), ("pro", "Pro"), ("team", "Team"), ("enterprise", "Enterprise")]
    STATUS_CHOICES = [("trialing", "Trialing"), ("active", "Active"), ("past_due", "Past due"), ("canceled", "Canceled")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    provider_customer_id = models.CharField(max_length=180, blank=True, default="")
    provider_subscription_id = models.CharField(max_length=180, blank=True, default="")
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class BillingEvent(models.Model):
    """Idempotency ledger for provider webhooks. A Stripe event is applied once."""
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=120)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["event_type", "-processed_at"], name="api_billing_event_type_9c6a0b_idx")]


class UsageRecord(models.Model):
    """Metered product usage, aggregated by user and UTC month."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="usage_records")
    period = models.DateField()
    metric = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "period", "metric"], name="unique_user_usage_period_metric")]
        indexes = [models.Index(fields=["user", "period"], name="api_usage_r_user_id_3d6fcb_idx"), models.Index(fields=["metric", "period"], name="api_usage_r_metric_1c7b4d_idx")]


class OrganizationInvite(models.Model):
    ROLE_CHOICES = [("admin", "Admin"), ("developer", "Developer"), ("viewer", "Viewer")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invites")
    inviter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="organization_invites_sent")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="developer")
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "email"], name="api_orginvi_organiz_1e3f55_idx"), models.Index(fields=["token"], name="api_orginvi_token_2e4b72_idx")]


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=120)
    target_type = models.CharField(max_length=80, blank=True, default="")
    target_id = models.CharField(max_length=120, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "-created_at"], name="api_auditlo_organiz_2c6f42_idx"), models.Index(fields=["user", "-created_at"], name="api_auditlo_user_id_9d8b23_idx")]


class APIKey(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16)
    key_hash = models.CharField(max_length=128, unique=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "revoked_at"], name="api_securit_user_id_0a7b20_idx")]


# =========================================================
# DEVELOPER OS 4.0 — Mature SaaS control plane
# =========================================================

class EmailVerificationToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verification_tokens")
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]


class SecuritySession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="security_sessions")
    jti = models.CharField(max_length=255, unique=True)
    device_name = models.CharField(max_length=200, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "revoked_at"]), models.Index(fields=["user", "-last_seen_at"], name="api_securit_user_id_72e6d1_idx")]


class MFADevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mfa_device")
    secret = EncryptedTextField()
    enabled = models.BooleanField(default=False)
    backup_code_hashes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)


class LoginAttempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="login_attempts")
    identifier = models.CharField(max_length=254)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    success = models.BooleanField(default=False)
    reason = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["identifier", "-created_at"], name="api_loginat_identif_1fdc3b_idx"), models.Index(fields=["ip_address", "-created_at"], name="api_loginat_ip_addr_9c1d5b_idx")]


class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preferences")
    product_updates = models.BooleanField(default=True)
    security_alerts = models.BooleanField(default=True)
    team_activity = models.BooleanField(default=True)
    billing = models.BooleanField(default=True)
    marketing = models.BooleanField(default=False)
    email_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrganizationSubscription(models.Model):
    STATUS_CHOICES = [("trialing", "Trialing"), ("active", "Active"), ("past_due", "Past due"), ("canceled", "Canceled")]
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="subscription_record")
    plan = models.CharField(max_length=20, choices=Organization.PLAN_CHOICES, default="free")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    provider_customer_id = models.CharField(max_length=180, blank=True, default="")
    provider_subscription_id = models.CharField(max_length=180, blank=True, default="")
    price_id = models.CharField(max_length=180, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class BillingInvoice(models.Model):
    STATUS_CHOICES = [("draft", "Draft"), ("open", "Open"), ("paid", "Paid"), ("void", "Void"), ("uncollectible", "Uncollectible")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invoices", null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="billing_invoices", null=True, blank=True)
    provider_invoice_id = models.CharField(max_length=180, unique=True)
    number = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="open")
    currency = models.CharField(max_length=8, default="usd")
    subtotal = models.BigIntegerField(default=0)
    tax = models.BigIntegerField(default=0)
    total = models.BigIntegerField(default=0)
    amount_due = models.BigIntegerField(default=0)
    hosted_url = models.URLField(blank=True, default="")
    invoice_pdf = models.URLField(blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentAttempt(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("succeeded", "Succeeded"), ("failed", "Failed")]
    invoice = models.ForeignKey(BillingInvoice, on_delete=models.CASCADE, related_name="payment_attempts")
    provider_payment_id = models.CharField(max_length=180, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    amount = models.BigIntegerField(default=0)
    failure_code = models.CharField(max_length=120, blank=True, default="")
    failure_message = models.TextField(blank=True, default="")
    attempted_at = models.DateTimeField(auto_now_add=True)


class BillingCredit(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="billing_credits", null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="billing_credits", null=True, blank=True)
    amount = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default="usd")
    reason = models.CharField(max_length=240)
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BackgroundJob(models.Model):
    STATUS_CHOICES = [("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("canceled", "Canceled")]
    kind = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=120, blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    dead_lettered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at"], name="api_backgro_status_53ef0d_idx"),
            models.Index(fields=["kind", "-created_at"], name="api_backgro_kind_1d4b0f_idx"),
            models.Index(fields=["idempotency_key", "kind"], name="api_backgr_idem_k_3e3e8b_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_job_kind_idempotency",
            )
        ]


class DataExportRequest(models.Model):
    STATUS_CHOICES = [("queued", "Queued"), ("running", "Running"), ("ready", "Ready"), ("failed", "Failed"), ("expired", "Expired")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_export_requests")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    file_path = models.CharField(max_length=500, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class AccountDeletionRequest(models.Model):
    STATUS_CHOICES = [("pending", "Pending"), ("confirmed", "Confirmed"), ("completed", "Completed"), ("canceled", "Canceled")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="deletion_request")
    token_hash = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    scheduled_for = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)


class SupportTicket(models.Model):
    STATUS_CHOICES = [("open", "Open"), ("pending", "Pending"), ("resolved", "Resolved"), ("closed", "Closed")]
    PRIORITY_CHOICES = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=240)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_support_tickets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ProductEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="product_events", null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="product_events", null=True, blank=True)
    name = models.CharField(max_length=120)
    properties = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["name", "-occurred_at"], name="api_product_name_9f2d11_idx"), models.Index(fields=["user", "name", "-occurred_at"], name="api_product_user_id_0cc22f_idx")]


class Incident(models.Model):
    STATUS_CHOICES = [("investigating", "Investigating"), ("identified", "Identified"), ("monitoring", "Monitoring"), ("resolved", "Resolved")]
    SEVERITY_CHOICES = [("sev1", "SEV1"), ("sev2", "SEV2"), ("sev3", "SEV3"), ("sev4", "SEV4")]
    title = models.CharField(max_length=240)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="investigating")
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="sev3")
    summary = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="incidents_created")


class ObjectStorageFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="object_files")
    key = models.CharField(max_length=500, unique=True)
    bucket = models.CharField(max_length=160, blank=True, default="")
    content_type = models.CharField(max_length=180, blank=True, default="application/octet-stream")
    size = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=128, blank=True, default="")
    provider = models.CharField(max_length=40, default="local")
    created_at = models.DateTimeField(auto_now_add=True)

class OrganizationRolePermission(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="role_permissions")
    role = models.CharField(max_length=40)
    permissions = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "role"], name="unique_org_role_permission")]


class WorkspaceCollaborationSession(models.Model):
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name="collaboration_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="workspace_collaboration_sessions")
    client_id = models.CharField(max_length=96)
    cursor = models.JSONField(default=dict, blank=True)
    selection = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["workspace", "user", "client_id"], name="unique_workspace_collab_client"),
        ]
        indexes = [models.Index(fields=["workspace", "-last_seen_at"], name="api_ws_collab_last_seen_idx")]

class WorkspaceFileRevision(models.Model):
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name="file_revisions")
    path = models.CharField(max_length=500)
    revision = models.PositiveBigIntegerField()
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    client_id = models.CharField(max_length=96, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["workspace", "path", "-revision"], name="api_ws_file_rev_idx")]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "path", "revision"], name="unique_workspace_file_revision"),
        ]


class WorkspaceFileLock(models.Model):
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name='file_locks')
    path = models.CharField(max_length=500)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=96)
    acquired_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace','path'], name='unique_workspace_file_lock')]
        indexes = [models.Index(fields=['workspace','path'], name='api_ws_file_lock_idx')]

class WorkspaceCRDTOperation(models.Model):
    workspace = models.ForeignKey(CodeWorkspace, on_delete=models.CASCADE, related_name="crdt_operations")
    path = models.CharField(max_length=500)
    operation_id = models.CharField(max_length=128, unique=True)
    actor_id = models.CharField(max_length=128)
    lamport = models.PositiveBigIntegerField(default=0)
    kind = models.CharField(max_length=16)
    position = models.PositiveIntegerField(default=0)
    delete_count = models.PositiveIntegerField(default=0)
    text = models.TextField(blank=True, default="")
    update_blob = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    class Meta:
        indexes = [
            models.Index(fields=["workspace", "path", "lamport"], name="api_crdt_path_lam_idx"),
            models.Index(fields=["workspace", "path", "created_at"], name="api_crdt_path_created_idx"),
        ]
