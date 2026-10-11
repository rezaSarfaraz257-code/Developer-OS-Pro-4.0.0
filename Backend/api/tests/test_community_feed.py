from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from api.models import CommunityPost, CommunityPostComment


class CommunityFeedAPITests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test-password-123")
        self.bob = User.objects.create_user(username="bob", password="test-password-123")
        self.posts_url = reverse("community-posts")

    def test_posts_are_shared_across_authenticated_accounts(self):
        post = CommunityPost.objects.create(author=self.alice, body="Shipping a new build today.")
        self.client.force_authenticate(user=self.bob)

        response = self.client.get(self.posts_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], post.id)
        self.assertEqual(response.data["results"][0]["author_username"], "alice")
        self.assertFalse(response.data["results"][0]["liked_by_me"])

    def test_post_requires_nonempty_body_and_enforces_length(self):
        self.client.force_authenticate(user=self.alice)

        empty = self.client.post(self.posts_url, {"body": "   "}, format="json")
        too_long = self.client.post(self.posts_url, {"body": "x" * 3001}, format="json")

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(CommunityPost.objects.count(), 0)

    def test_like_endpoint_toggles_without_duplicate_likes(self):
        post = CommunityPost.objects.create(author=self.alice, body="A post")
        self.client.force_authenticate(user=self.bob)
        url = reverse("community-post-like", kwargs={"pk": post.pk})

        liked = self.client.post(url, {}, format="json")
        liked_again = self.client.post(url, {}, format="json")
        unliked = self.client.post(url, {}, format="json")

        self.assertEqual(liked.status_code, 200)
        self.assertTrue(liked.data["liked"])
        self.assertEqual(liked.data["like_count"], 1)
        self.assertFalse(liked_again.data["liked"])
        self.assertTrue(unliked.data["liked"])
        self.assertEqual(post.likes.count(), 1)

    def test_comment_permissions_and_post_author_deletion(self):
        post = CommunityPost.objects.create(author=self.alice, body="A post")
        self.client.force_authenticate(user=self.bob)
        comments_url = reverse("community-post-comments", kwargs={"pk": post.pk})
        created = self.client.post(comments_url, {"body": "Helpful feedback"}, format="json")
        comment_id = created.data["id"]

        forbidden = self.client.delete(reverse("community-post-comment-detail", kwargs={"pk": post.pk, "comment_id": comment_id}))
        self.assertEqual(created.status_code, 201)
        self.assertEqual(forbidden.status_code, 204)
        self.assertEqual(CommunityPostComment.objects.count(), 0)

    def test_only_author_can_delete_a_post(self):
        post = CommunityPost.objects.create(author=self.alice, body="A post")
        self.client.force_authenticate(user=self.bob)
        response = self.client.delete(reverse("community-post-detail", kwargs={"pk": post.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(CommunityPost.objects.filter(pk=post.pk).exists())
