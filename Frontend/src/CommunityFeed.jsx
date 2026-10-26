import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "./services/api";
import "./CommunityFeed.css";

function getPostTopic(post) {
  const text = String(post?.body || "").toLowerCase();
  if (/\b(ai|llm|model|prompt|agent|machine learning|artificial intelligence)\b/.test(text)) return "AI";
  if (/\b(python|django|fastapi|flask)\b/.test(text)) return "Python";
  if (/\b(react|javascript|typescript|css|html|frontend|front-end|web app)\b/.test(text)) return "Web";
  if (/\b(learn|learning|lesson|tutorial|study|course|beginner)\b/.test(text)) return "Learning";
  if (/\b(build|building|project|launch|ship|release|deploy|startup)\b/.test(text)) return "Projects";
  return "General";
}

function readSavedPosts() {
  try {
    const value = JSON.parse(window.localStorage.getItem("developer-os-community-saved") || "[]");
    return Array.isArray(value) ? value.filter(id => typeof id === "number" || typeof id === "string") : [];
  } catch {
    return [];
  }
}

function formatTime(value) {
  if (!value) return "Just now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Just now";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
  if (seconds < 604800) return Math.floor(seconds / 86400) + "d ago";
  return date.toLocaleDateString();
}

async function responseData(response, fallback) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || fallback);
  return data;
}

export default function CommunityFeed() {
  const [posts, setPosts] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [likeBusy, setLikeBusy] = useState([]);
  const [expanded, setExpanded] = useState([]);
  const [comments, setComments] = useState({});
  const [commentDrafts, setCommentDrafts] = useState({});
  const [commentLoading, setCommentLoading] = useState([]);
  const [commentPosting, setCommentPosting] = useState([]);
  const [feedQuery, setFeedQuery] = useState("");
  const [feedView, setFeedView] = useState("all");
  const [feedSort, setFeedSort] = useState("newest");
  const [feedTopic, setFeedTopic] = useState("All topics");
  const [savedPostIds, setSavedPostIds] = useState(readSavedPosts);
  const searchInputRef = useRef(null);
  const requestSequence = useRef(0);

  useEffect(() => {
    try {
      window.localStorage.setItem("developer-os-community-saved", JSON.stringify(savedPostIds));
    } catch {
      // Saving is a convenience; private browsing or storage restrictions should not break the feed.
    }
  }, [savedPostIds]);

  useEffect(() => {
    const onKeyDown = event => {
      const target = event.target;
      const typing = target instanceof HTMLElement && (
        target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)
      );
      if (!typing && event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        searchInputRef.current?.focus();
      }
      if (event.key === "Escape" && target === searchInputRef.current) {
        setFeedQuery("");
        searchInputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const loadPosts = useCallback(async (beforeId = null, append = false) => {
    const requestId = ++requestSequence.current;
    if (!append) setLoading(true);
    setError("");
    try {
      const suffix = beforeId ? `?before_id=${encodeURIComponent(beforeId)}` : "";
      const response = await apiFetch(`/community/posts/${suffix}`);
      const payload = await responseData(response, "Community feed could not be loaded.");
      if (requestId !== requestSequence.current) return;
      const results = Array.isArray(payload) ? payload : (payload.results || []);
      setHasMore(Boolean(payload.next_before_id));
      setPosts(previous => append ? [...previous, ...results.filter(p => !previous.some(old => old.id === p.id))] : results);
    } catch (err) {
      if (requestId === requestSequence.current) setError(err?.message || "Community feed could not be loaded.");
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void loadPosts(); }, [loadPosts]);

  const publish = async (event) => {
    event.preventDefault();
    const body = draft.trim();
    if (!body || posting) return;
    setPosting(true); setError(""); setNotice("");
    try {
      const response = await apiFetch("/community/posts/", { method: "POST", body: JSON.stringify({ body }) });
      const post = await responseData(response, "Your post could not be published.");
      setPosts(previous => [post, ...previous]);
      setDraft("");
      setNotice("Your post is live in the community feed.");
    } catch (err) {
      setError(err?.message || "Your post could not be published.");
    } finally {
      setPosting(false);
    }
  };

  const toggleLike = async (post) => {
    if (likeBusy.includes(post.id)) return;
    setLikeBusy(previous => [...previous, post.id]);
    setError("");
    try {
      const response = await apiFetch(`/community/posts/${post.id}/like/`, { method: "POST", body: JSON.stringify({}) });
      const result = await responseData(response, "Like could not be updated.");
      setPosts(previous => previous.map(item => item.id === post.id ? { ...item, liked_by_me: result.liked, like_count: result.like_count } : item));
    } catch (err) {
      setError(err?.message || "Like could not be updated.");
    } finally {
      setLikeBusy(previous => previous.filter(id => id !== post.id));
    }
  };

  const toggleComments = async (postId) => {
    if (expanded.includes(postId)) {
      setExpanded(previous => previous.filter(id => id !== postId));
      return;
    }
    setExpanded(previous => [...previous, postId]);
    if (comments[postId]) return;
    setCommentLoading(previous => [...previous, postId]);
    try {
      const response = await apiFetch(`/community/posts/${postId}/comments/`);
      const payload = await responseData(response, "Comments could not be loaded.");
      setComments(previous => ({ ...previous, [postId]: Array.isArray(payload) ? payload : (payload.results || []) }));
    } catch (err) {
      setError(err?.message || "Comments could not be loaded.");
    } finally {
      setCommentLoading(previous => previous.filter(id => id !== postId));
    }
  };

  const publishComment = async (event, post) => {
    event.preventDefault();
    const body = (commentDrafts[post.id] || "").trim();
    if (!body || commentPosting.includes(post.id)) return;
    setCommentPosting(previous => [...previous, post.id]); setError("");
    try {
      const response = await apiFetch(`/community/posts/${post.id}/comments/`, { method: "POST", body: JSON.stringify({ body }) });
      const comment = await responseData(response, "Comment could not be published.");
      setComments(previous => ({ ...previous, [post.id]: [...(previous[post.id] || []), comment] }));
      setCommentDrafts(previous => ({ ...previous, [post.id]: "" }));
      setPosts(previous => previous.map(item => item.id === post.id ? { ...item, comment_count: (item.comment_count || 0) + 1 } : item));
    } catch (err) {
      setError(err?.message || "Comment could not be published.");
    } finally {
      setCommentPosting(previous => previous.filter(id => id !== post.id));
    }
  };

  const sharePost = async (post) => {
    const url = new URL(window.location.href);
    url.hash = `community-post-${post.id}`;
    const shareData = {
      title: `${post.author_username || "Developer"} · Developer OS Community`,
      text: String(post.body || "").slice(0, 220),
      url: url.toString(),
    };
    try {
      if (typeof navigator.share === "function") {
        await navigator.share(shareData);
        setNotice("Post shared successfully.");
      } else if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url.toString());
        setNotice("Post link copied. Share it with the community.");
      } else {
        setNotice(`Post link: ${url.toString()}`);
      }
    } catch (err) {
      if (err?.name !== "AbortError") setError("This browser could not share the post. Try copying the page URL.");
    }
  };
  const deletePost = async (post) => {
    if (!window.confirm("Delete this post permanently?")) return;
    setError("");
    try {
      const response = await apiFetch(`/community/posts/${post.id}/`, { method: "DELETE" });
      if (!response.ok) await responseData(response, "Post could not be deleted.");
      setPosts(previous => previous.filter(item => item.id !== post.id));
    } catch (err) {
      setError(err?.message || "Post could not be deleted.");
    }
  };

  const deleteComment = async (post, comment) => {
    setError("");
    try {
      const response = await apiFetch(`/community/posts/${post.id}/comments/${comment.id}/`, { method: "DELETE" });
      if (!response.ok) await responseData(response, "Comment could not be deleted.");
      setComments(previous => ({ ...previous, [post.id]: (previous[post.id] || []).filter(item => item.id !== comment.id) }));
      setPosts(previous => previous.map(item => item.id === post.id ? { ...item, comment_count: Math.max(0, (item.comment_count || 0) - 1) } : item));
    } catch (err) {
      setError(err?.message || "Comment could not be deleted.");
    }
  };

  const visiblePosts = useMemo(() => {
    const needle = feedQuery.trim().toLowerCase();
    const filtered = posts.filter(post => {
      if (feedView === "mine" && !post.is_author) return false;
      if (feedView === "liked" && !post.liked_by_me) return false;
      if (feedView === "saved" && !savedPostIds.includes(post.id)) return false;
      if (feedTopic !== "All topics" && getPostTopic(post) !== feedTopic) return false;
      return !needle || `${post.author_username || ""} ${post.body || ""} ${getPostTopic(post)}`.toLowerCase().includes(needle);
    });
    return filtered.sort((a, b) => {
      if (feedSort === "popular") return (Number(b.like_count || 0) + Number(b.comment_count || 0) * 2) - (Number(a.like_count || 0) + Number(a.comment_count || 0) * 2) || new Date(b.created_at || 0) - new Date(a.created_at || 0);
      if (feedSort === "liked") return Number(b.like_count || 0) - Number(a.like_count || 0) || new Date(b.created_at || 0) - new Date(a.created_at || 0);
      if (feedSort === "oldest") return new Date(a.created_at || 0) - new Date(b.created_at || 0);
      return new Date(b.created_at || 0) - new Date(a.created_at || 0) || Number(b.id || 0) - Number(a.id || 0);
    });
  }, [posts, feedQuery, feedView, feedSort, feedTopic, savedPostIds]);
  const loadedLikes = posts.reduce((sum, post) => sum + Number(post.like_count || 0), 0);
  const activeDiscussions = posts.filter(post => Number(post.comment_count || 0) > 0).length;

  return <section className="community-feed">
    <header className="community-heading">
      <div><span className="community-eyebrow">DEVELOPER OS / COMMUNITY NETWORK</span><h2>Build in public. <em>Grow together.</em></h2><p>One shared space for developers to publish ideas, show progress, give feedback and connect across every account.</p></div>
      <div className="community-live"><i /> PUBLIC NETWORK <b>{posts.length} loaded</b></div>
    </header>

    <div className="community-network-stats" aria-label="Community feed summary"><div><span>POSTS LOADED</span><strong>{posts.length}</strong></div><div><span>REACTIONS</span><strong>{loadedLikes}</strong></div><div><span>ACTIVE DISCUSSIONS</span><strong>{activeDiscussions}</strong></div><div className="community-network-note"><span>BUILD IN PUBLIC</span><strong>Ideas grow when shared.</strong></div></div>

    <form className="community-composer" onSubmit={publish}>
      <div className="community-composer-top"><div className="community-composer-mark">✦</div><div><strong>Share with the community</strong><span>Your post will be visible to Developer OS members.</span></div><span className="community-public-chip">PUBLIC</span></div>
      <textarea value={draft} onChange={event => setDraft(event.target.value.slice(0, 3000))} maxLength={3000} rows={4} placeholder="What are you building, learning, or thinking about today?" aria-label="Write a public community post" />
      <div className="community-writing-prompts" aria-label="Post ideas"><span>NEED AN IDEA?</span><button type="button" onClick={() => setDraft("🚀 What I’m building this week:\n\n🎯 Goal: \n🛠️ Stack: \n💡 Looking for feedback on: ")}>Build log</button><button type="button" onClick={() => setDraft("💡 I have an idea for a developer tool:\n\nProblem: \nMy idea: \nWho would benefit: ")}>Share an idea</button><button type="button" onClick={() => setDraft("🧠 Something I learned recently:\n\nThe lesson: \nHow I used it: \nWhat I’d tell another developer: ")}>Learning note</button></div>
      <div className="community-composer-footer"><span>{draft.length.toLocaleString()} / 3,000 characters · Be respectful. Never share secrets or access tokens.</span><button type="submit" disabled={!draft.trim() || posting}>{posting ? "PUBLISHING…" : "PUBLISH POST"} <b>↗</b></button></div>
    </form>

    {error && <div className="community-alert" role="alert">{error}</div>}
    {notice && <div className="community-notice" role="status">{notice}</div>}

    <div className="community-feed-toolbar"><div><span>THE NETWORK</span><h3>Developer conversations</h3><p>Discover ideas, share progress and join the discussion.</p></div><button type="button" onClick={() => void loadPosts()} disabled={loading}>{loading ? "SYNCING…" : "↻ Refresh feed"}</button></div>

    <div className="community-discovery-bar"><label className="community-feed-search"><span>⌕</span><input ref={searchInputRef} value={feedQuery} onChange={event => setFeedQuery(event.target.value)} placeholder="Search posts or developers…" aria-label="Search loaded community posts"/>{feedQuery && <button type="button" onClick={() => setFeedQuery("")} aria-label="Clear search">×</button>}</label><div className="community-feed-filters" role="group" aria-label="Filter posts">{[["all","All posts"],["mine","My posts"],["liked","Liked"],["saved","Saved"]].map(([id,label]) => <button key={id} type="button" className={feedView === id ? "active" : ""} aria-pressed={feedView === id} onClick={() => setFeedView(id)}>{label}</button>)}</div><label className="community-feed-sort"><span>SORT</span><select value={feedSort} onChange={event => setFeedSort(event.target.value)} aria-label="Sort community posts"><option value="newest">Newest</option><option value="popular">Most discussed</option><option value="liked">Most liked</option><option value="oldest">Oldest</option></select></label></div>
    <div className="community-topic-rail" aria-label="Explore topics">{["All topics","AI","Web","Python","Learning","Projects","General"].map(topic => <button type="button" key={topic} className={feedTopic === topic ? "active" : ""} aria-pressed={feedTopic === topic} onClick={() => setFeedTopic(topic)}>{topic}{topic === "All topics" ? <span>{posts.length}</span> : <span>{posts.filter(post => getPostTopic(post) === topic).length}</span>}</button>)}</div>
    <div className="community-result-summary" aria-live="polite"><span>{visiblePosts.length} matching posts</span><span>Press / to search · Search, topics and filters apply to loaded posts</span></div>

    {loading && posts.length === 0 ? <div className="community-loading" role="status" aria-label="Loading community posts"><i/><i/><i/></div> : posts.length === 0 && error ? <div className="community-empty community-empty-error" role="alert"><div>!</div><h3>Community feed unavailable</h3><p>{error}</p><button type="button" onClick={() => void loadPosts()} disabled={loading}>{loading ? "Retrying…" : "Retry loading posts"}</button></div> : posts.length === 0 ? <div className="community-empty"><div>◌</div><h3>The next great idea could be yours.</h3><p>Start the conversation by sharing what you are working on.</p></div> : visiblePosts.length === 0 ? <div className="community-filter-empty"><div>⌕</div><h3>No matching conversations</h3><p>Try a different search or switch the feed filter.</p><button type="button" onClick={() => { setFeedQuery(""); setFeedView("all"); setFeedTopic("All topics"); }}>Clear search and filters</button></div> :
      <div className="community-post-list">{visiblePosts.map(post => <article className="community-post" id={`community-post-${post.id}`} key={post.id}>
        <div className="community-post-header"><div className="community-avatar">{String(post.author_username || "D").slice(0, 1).toUpperCase()}</div><div className="community-author"><strong>{post.author_username || "Developer"}</strong><span>Developer OS member · {formatTime(post.created_at)}</span></div><span className="community-post-badge">{getPostTopic(post).toUpperCase()}</span>{post.is_author && <button type="button" className="community-delete" onClick={() => void deletePost(post)} aria-label="Delete your post" title="Delete post">⋯</button>}</div>
        <div className="community-post-body">{post.body}</div>
        <div className="community-post-stats"><span>{post.like_count || 0} {(post.like_count || 0) === 1 ? "like" : "likes"}</span><span>{post.comment_count || 0} {(post.comment_count || 0) === 1 ? "comment" : "comments"}</span></div>
        <div className="community-post-actions"><button type="button" className={post.liked_by_me ? "is-liked" : ""} onClick={() => void toggleLike(post)} disabled={likeBusy.includes(post.id)} aria-pressed={!!post.liked_by_me}><span>{post.liked_by_me ? "♥" : "♡"}</span> {post.liked_by_me ? "Liked" : "Like"}</button><button type="button" onClick={() => void toggleComments(post.id)} aria-expanded={expanded.includes(post.id)}><span>▤</span> {expanded.includes(post.id) ? "Hide comments" : "Comment"}</button><button type="button" onClick={() => void sharePost(post)} aria-label={`Share post by ${post.author_username || "developer"}`}><span>↗</span> Share</button><button type="button" className={savedPostIds.includes(post.id) ? "community-save is-saved" : "community-save"} onClick={() => setSavedPostIds(previous => previous.includes(post.id) ? previous.filter(id => id !== post.id) : [...previous, post.id])} aria-pressed={savedPostIds.includes(post.id)} aria-label={savedPostIds.includes(post.id) ? "Remove saved post" : "Save post"}><span>{savedPostIds.includes(post.id) ? "★" : "☆"}</span> {savedPostIds.includes(post.id) ? "Saved" : "Save"}</button><span className="community-action-spacer"/><span className="community-post-id">POST / {String(post.id).padStart(4, "0")}</span></div>
        {expanded.includes(post.id) && <div className="community-comments">
          {commentLoading.includes(post.id) ? <p className="community-comment-hint">Loading conversation…</p> : (comments[post.id] || []).length === 0 ? <p className="community-comment-hint">No comments yet. Start a thoughtful conversation.</p> : (comments[post.id] || []).map(comment => <div className="community-comment" key={comment.id}><div className="community-comment-avatar">{String(comment.author_username || "D").slice(0, 1).toUpperCase()}</div><div className="community-comment-content"><div><strong>{comment.author_username || "Developer"}</strong><time>{formatTime(comment.created_at)}</time></div><p>{comment.body}</p></div>{(comment.is_author || post.is_author) && <button type="button" onClick={() => void deleteComment(post, comment)} aria-label="Delete comment">×</button>}</div>)}
          <form className="community-comment-form" onSubmit={event => void publishComment(event, post)}><input value={commentDrafts[post.id] || ""} onChange={event => setCommentDrafts(previous => ({ ...previous, [post.id]: event.target.value.slice(0, 1200) }))} maxLength={1200} placeholder="Add a useful comment…" aria-label="Write a comment"/><button type="submit" disabled={!(commentDrafts[post.id] || "").trim() || commentPosting.includes(post.id)}>{commentPosting.includes(post.id) ? "…" : "Reply ↗"}</button></form>
        </div>}
      </article>)}</div>
    }
    {!loading && hasMore && <div className="community-load-more"><button type="button" onClick={() => void loadPosts(posts[posts.length - 1]?.id, true)}>LOAD OLDER POSTS ↓</button></div>}
  </section>;
}
