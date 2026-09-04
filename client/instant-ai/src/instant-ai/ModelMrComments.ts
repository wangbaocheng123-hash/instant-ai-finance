import type { ModelMrComment } from './types';

export interface ModelMrCommentThread {
  key: string;
  root: ModelMrComment | null;
  replies: ModelMrComment[];
}

// Identity comes from the collector's explicit marker, never a lookalike nickname
// or a kind such as author_liked_comment (which describes an ordinary reader).
export const isAuthorComment = (comment: ModelMrComment): boolean =>
  ['author', 'author_comment', 'author_reply'].includes(comment.kind);

export function effectiveCommentText(text: string): string {
  return text.replace(/\[[^\]]{1,24}\]/g, '').replace(/@\S+/g, '')
    .replace(/[^A-Za-z0-9\u4e00-\u9fff]+/g, '').toLowerCase()
    .replace(/(.{1,4})\1{3,}/g, '$1');
}

export function isLowValueComment(text: string): boolean {
  const compact = effectiveCommentText(text);
  return !compact || /^(哈|呵|嘿|嘻|6|嗯|哦|啊|好|赞|点赞|支持|收到|路过|来了|谢谢|感谢|学习了|关注了|蹲一个|蹲)+$/.test(compact);
}

export function commentThreads(comments: ModelMrComment[]): ModelMrCommentThread[] {
  const threads = new Map<string, ModelMrCommentThread>();
  comments.forEach((comment) => {
    const key = comment.thread_key || `comment-${comment.id}`;
    let thread = threads.get(key);
    if (!thread) { thread = { key, root: null, replies: [] }; threads.set(key, thread); }
    if (!comment.reply_depth && !thread.root) thread.root = comment;
    else thread.replies.push(comment);
  });
  return Array.from(threads.values());
}

export function threadHasAuthorInteraction(thread: ModelMrCommentThread): boolean {
  return [thread.root, ...thread.replies].some(comment => comment && (isAuthorComment(comment) || comment.author_liked));
}

export function rankCommentThreads(threads: ModelMrCommentThread[]): { topLiked: ModelMrCommentThread[]; remaining: ModelMrCommentThread[] } {
  const candidates = threads.filter(thread => {
    const lead = thread.root || thread.replies[0];
    return lead && !isAuthorComment(lead) && !isLowValueComment(lead.text);
  });
  const lead = (thread: ModelMrCommentThread) => (thread.root || thread.replies[0])!;
  const length = (thread: ModelMrCommentThread) => Math.min(effectiveCommentText(lead(thread).text).length, 500);
  const replies = (thread: ModelMrCommentThread) => Math.max(lead(thread).reply_count, thread.replies.length);
  const byLikes = (a: ModelMrCommentThread, b: ModelMrCommentThread) =>
    lead(b).like_count - lead(a).like_count || replies(b) - replies(a) || length(b) - length(a);
  // Fewer than ten positive-like threads are not padded with zero-like comments.
  const topLiked = candidates.filter(thread => lead(thread).like_count > 0).sort(byLikes).slice(0, 10);
  const topKeys = new Set(topLiked.map(thread => thread.key));
  const remaining = candidates.filter(thread => !topKeys.has(thread.key)).sort((a, b) =>
    Number(length(b) >= 20) - Number(length(a) >= 20) || length(b) - length(a) || replies(b) - replies(a) || lead(b).like_count - lead(a).like_count);
  return { topLiked, remaining };
}
