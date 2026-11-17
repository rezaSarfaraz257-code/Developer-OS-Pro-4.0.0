from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Organization, OrganizationInvite, OrganizationMembership

User = get_user_model()


class OrganizationSeatReservationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="seat-owner",
            email="owner@example.test",
            password="StrongPass123!",
        )
        self.org = Organization.objects.create(
            owner=self.owner,
            name="Seat Reservation Org",
            slug="seat-reservation-org",
            plan="team",
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.owner,
            role="owner",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.invite_url = f"/api/organizations/{self.org.id}/invites/"
        self.members_url = f"/api/organizations/{self.org.id}/members/"

    def create_invite(self, email):
        return self.client.post(
            self.invite_url,
            {"email": email, "role": "developer"},
            format="json",
        )

    def test_pending_invitation_reserves_the_last_available_seat(self):
        from unittest.mock import patch

        with patch("api.views._organization_seat_limit", return_value=2):
            first = self.create_invite("first@example.test")
            self.assertEqual(first.status_code, 201, first.json())

            second = self.create_invite("second@example.test")
            self.assertEqual(second.status_code, 403, second.json())

        self.assertEqual(
            OrganizationInvite.objects.filter(
                organization=self.org,
                accepted_at__isnull=True,
            ).count(),
            1,
        )

    def test_direct_member_add_cannot_consume_a_seat_reserved_by_invite(self):
        from unittest.mock import patch

        invite = None
        with patch("api.views._organization_seat_limit", return_value=2):
            invite = self.create_invite("reserved@example.test")
            self.assertEqual(invite.status_code, 201, invite.json())

            new_user = User.objects.create_user(
                username="seat-direct-member",
                email="direct@example.test",
                password="StrongPass123!",
            )
            response = self.client.post(
                self.members_url,
                {"username": new_user.username, "role": "developer"},
                format="json",
            )
            self.assertEqual(response.status_code, 403, response.json())
            self.assertEqual(response.json()["code"], "seat_limit_reached")

        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=new_user,
            ).exists()
        )

    def test_invite_acceptance_rechecks_capacity_if_seats_were_consumed(self):
        from unittest.mock import patch

        invited_user = User.objects.create_user(
            username="seat-invitee",
            email="invitee@example.test",
            password="StrongPass123!",
        )
        with patch("api.views._organization_seat_limit", return_value=3):
            invite_response = self.create_invite(invited_user.email)
            self.assertEqual(invite_response.status_code, 201, invite_response.json())

            for index in range(2):
                member = User.objects.create_user(
                    username=f"seat-member-{index}",
                    email=f"member-{index}@example.test",
                    password="StrongPass123!",
                )
                OrganizationMembership.objects.create(
                    organization=self.org,
                    user=member,
                    role="developer",
                )

            invite_client = APIClient()
            invite_client.force_authenticate(invited_user)
            invite_response = invite_client.post(
                "/api/organizations/invites/accept/",
                {"token": invite_response.data["token"]},
                format="json",
            )

        self.assertEqual(invite_response.status_code, 403, invite_response.json())
        self.assertEqual(invite_response.data["code"], "seat_limit_reached")
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=invited_user,
            ).exists()
        )
        invite = OrganizationInvite.objects.get(
            organization=self.org,
            email=invited_user.email,
        )
        self.assertIsNone(invite.accepted_at)

    def test_reserved_invitation_can_be_accepted_when_capacity_remains(self):
        from unittest.mock import patch

        invited_user = User.objects.create_user(
            username="seat-valid-invitee",
            email="valid-invitee@example.test",
            password="StrongPass123!",
        )
        with patch("api.views._organization_seat_limit", return_value=2):
            invite_response = self.create_invite(invited_user.email)
            self.assertEqual(invite_response.status_code, 201, invite_response.json())

            invite_client = APIClient()
            invite_client.force_authenticate(invited_user)
            response = invite_client.post(
                "/api/organizations/invites/accept/",
                {"token": invite_response.data["token"]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=invited_user,
            ).exists()
        )
        invite = OrganizationInvite.objects.get(
            organization=self.org,
            email=invited_user.email,
        )
        self.assertIsNotNone(invite.accepted_at)


    def test_invitation_acceptance_respects_a_reduced_seat_limit(self):
        from unittest.mock import patch

        invited_user = User.objects.create_user(
            username="seat-downgraded-invitee",
            email="downgraded-invitee@example.test",
            password="StrongPass123!",
        )
        with patch("api.views._organization_seat_limit", return_value=2):
            invite_response = self.create_invite(invited_user.email)
            self.assertEqual(invite_response.status_code, 201, invite_response.json())

        invite_client = APIClient()
        invite_client.force_authenticate(invited_user)
        with patch("api.views._organization_seat_limit", return_value=1):
            response = invite_client.post(
                "/api/organizations/invites/accept/",
                {"token": invite_response.data["token"]},
                format="json",
            )

        self.assertEqual(response.status_code, 403, response.json())
        self.assertEqual(response.data["code"], "seat_limit_reached")
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=invited_user,
            ).exists()
        )
        invite = OrganizationInvite.objects.get(
            organization=self.org,
            email=invited_user.email,
        )
        self.assertIsNone(invite.accepted_at)
    def test_member_add_rechecks_actor_role_after_organization_lock(self):
        target = User.objects.create_user(
            username="revoked-admin-target",
            email="revoked-target@example.test",
            password="StrongPass123!",
        )
        stale_membership = OrganizationMembership.objects.get(
            organization=self.org,
            user=self.owner,
        )

        def revoke_before_locked_check(*args, **kwargs):
            OrganizationMembership.objects.filter(pk=stale_membership.pk).delete()
            return stale_membership

        with patch("api.views._org_access", side_effect=revoke_before_locked_check):
            response = self.client.post(
                self.members_url,
                {"username": target.username, "role": "developer"},
                format="json",
            )

        self.assertEqual(response.status_code, 403, response.json())
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=target,
            ).exists()
        )

    def test_invitation_creation_rechecks_actor_role_after_organization_lock(self):
        stale_membership = OrganizationMembership.objects.get(
            organization=self.org,
            user=self.owner,
        )

        def revoke_before_locked_check(*args, **kwargs):
            OrganizationMembership.objects.filter(pk=stale_membership.pk).delete()
            return stale_membership

        with patch("api.views._org_access", side_effect=revoke_before_locked_check):
            response = self.create_invite("revoked-admin@example.test")

        self.assertEqual(response.status_code, 403, response.json())
        self.assertFalse(
            OrganizationInvite.objects.filter(
                organization=self.org,
                email="revoked-admin@example.test",
            ).exists()
        )
