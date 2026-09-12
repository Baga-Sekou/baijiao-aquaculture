const api = require('../../utils/api');

Page({
  data: { ponds: [], loading: true, error: '' },
  onShow() { this.load(); },
  load() {
    this.setData({ loading: true, error: '' });
    api.get('/api/ponds').then(({ data }) => {
      this.setData({ ponds: data, loading: false });
    }).catch((e) => {
      this.setData({ loading: false, error: e.message || '加载失败，请确认后端已启动' });
    });
  },
  openPond(e) {
    wx.navigateTo({ url: '/pages/pond/pond?id=' + e.currentTarget.dataset.id });
  },
});
