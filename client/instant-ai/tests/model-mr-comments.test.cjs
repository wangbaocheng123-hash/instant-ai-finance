const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modulePromise = import('data:text/javascript;base64,' + fs.readFileSync(path.join(process.env.MODEL_MR_TEST_BUILD, 'ModelMrComments.js')).toString('base64'));
const comment = (id, extra = {}) => ({ id, author: '读者', kind: 'user_comment', text: `有效问题${id}`, like_count: 0, reply_count: 0, reply_depth: 0, thread_key: `t${id}`, author_liked: false, published_at: '', ...extra });

test('author marker does not confuse author-liked or duplicate display names', async () => {
 const m = await modulePromise;
 assert(m.isAuthorComment(comment(1,{kind:'author_reply'})));
 assert(m.isAuthorComment(comment(2,{kind:'author_comment'})));
 assert(!m.isAuthorComment(comment(3,{kind:'author_liked_comment'})));
 assert(!m.isAuthorComment(comment(4,{author:'模型先生'})));
});
test('low-information replies do not become top-liked entries', async () => {
 const m = await modulePromise;
 for (const text of ['[赞][赞]','哈哈哈哈哈哈','支持支持支持支持','666666','😀']) assert(m.isLowValueComment(text));
 assert(!m.isLowValueComment('没有突破前高是否继续观察？'));
 assert(!m.isLowValueComment('2026 年怎么看？'));
});
test('top ten by likes and long-form remainder are separate with no duplicates', async () => {
 const m = await modulePromise;
 const input = Array.from({length:15},(_,i)=>comment(i+1,{like_count:100-i}));
 input.push(comment(99,{like_count:1,text:'这是较长的研究讨论，说明自己的依据与疑问，希望核对数据来源及风险边界。'}));
 input.push(comment(100,{like_count:999,text:'[赞]'}));
 const before=JSON.stringify(input);
 const ranked=m.rankCommentThreads(m.commentThreads(input));
 assert.deepEqual(ranked.topLiked.map(t=>t.root.id),[1,2,3,4,5,6,7,8,9,10]);
 assert.equal(ranked.remaining[0].root.id,99);
 assert.equal(new Set([...ranked.topLiked,...ranked.remaining].map(t=>t.key)).size,16);
 assert.equal(JSON.stringify(input),before);
});
test('less than ten positive-like comments are not padded', async () => {
 const m = await modulePromise;
 const ranked=m.rankCommentThreads(m.commentThreads([comment(1),comment(2,{like_count:2}),comment(3,{like_count:1})]));
 assert.equal(ranked.topLiked.length,2);
 assert.deepEqual(ranked.remaining.map(t=>t.root.id),[1]);
});
test('ties use reply count and effective text, then stable source order', async () => {
 const m = await modulePromise;
 const data=[comment(1,{like_count:5}),comment(2,{like_count:5,reply_count:2}),comment(3,{like_count:5,reply_count:2,text:'这是更完整的研究问题，包含背景与数据来源。'}),comment(4,{like_count:5})];
 assert.deepEqual(m.rankCommentThreads(m.commentThreads(data)).topLiked.map(t=>t.root.id),[3,2,1,4]);
});
test('author or liked reply retains original question and orphan reply is retained', async () => {
 const m = await modulePromise;
 const threads=m.commentThreads([comment(1),comment(2,{thread_key:'t1',reply_depth:1,kind:'author_reply'}),comment(3,{author_liked:true}),comment(4,{reply_depth:1})]);
 assert.equal(threads.length,3);
 assert.deepEqual(threads.filter(m.threadHasAuthorInteraction).map(t=>t.key),['t1','t3']);
 assert.equal(threads[0].root.id,1);
 assert.equal(threads[0].replies[0].id,2);
 assert.equal(threads[2].replies[0].id,4);
});
