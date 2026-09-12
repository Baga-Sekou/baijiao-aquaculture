const api = require('../../utils/api');

Page({
  data: { board: [], posts: [], title: '', content: '', catIndex: 0, cats: ['经验交流', '问题求助', '行情信息'] },
  onShow() { this.load(); },
  load() {
    api.get('/api/leaderboard?days=30').then(({ data }) => {
      this.setData({ board: data });
    }).catch(() => {});
    api.get('/api/community/posts').then(({ data }) => {
      this.setData({ posts: data });
    }).catch(() => {});
  },
  onInputTitle(e) { this.setData({ title: e.detail.value }); },
  onInputContent(e) { this.setData({ content: e.detail.value }); },
  onCatChange(e) { this.setData({ catIndex: +e.detail.value }); },
  publish() {
    const { title, content, cats, catIndex } = this.data;
    if (!title || !content) {
      wx.showToast({ title: '标题和内容不能为空', icon: 'none' });
      return;
    }
    api.post('/api/community/posts', { title, content, category: cats[catIndex] }).then(() => {
      wx.showToast({ title: '已发布', icon: 'success' });
      this.setData({ title: '', content: '' });
      this.load();
    }).catch((e) => wx.showToast({ title: e.message || '发布失败', icon: 'none' }));
  },
});
