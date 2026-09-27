from django.contrib import admin
from .models import (
    Project, EmailVerificationToken, SecuritySession, MFADevice, LoginAttempt,
    NotificationPreference, OrganizationSubscription, BillingInvoice, PaymentAttempt,
    BillingCredit, BackgroundJob, DataExportRequest, AccountDeletionRequest,
    SupportTicket, ProductEvent, Incident, ObjectStorageFile, OrganizationRolePermission,
)

admin.site.register([
    Project, EmailVerificationToken, SecuritySession, MFADevice, LoginAttempt,
    NotificationPreference, OrganizationSubscription, BillingInvoice, PaymentAttempt,
    BillingCredit, BackgroundJob, DataExportRequest, AccountDeletionRequest,
    SupportTicket, ProductEvent, Incident, ObjectStorageFile, OrganizationRolePermission,
])
