const api = require('../../utils/api');

Page({
  data: {
    id: 0, name: '', env: [], batch: null,
    suggestion: null, tasks: [], predict: null,
  },
  onLoad(q) { this.setData({ id: +q.id }); },
  onShow() { this.load(); },
  load() {
    const id = this.data.id;
    api.get('/api/ponds/' + id).then(({ data }) => {
      this.setData({
        name: data.name,
        batch: (data.batches || []).find((b) => b.status === 'active') || null,
      });
    }).catch(() => {});
    api.get(`/api/ponds/${id}/env/latest`).then(({ data }) => {
      const cn = { temperature: '水温', oxygen: '溶氧', ph: 'pH' };
      const env = Object.keys(cn).filter((k) => data[k] && data[k].connected)
        .map((k) => ({ name: cn[k], value: data[k].value, unit: data[k].unit,
                       time: data[k].collected_at }));
      this.setData({ env });
    }).catch(() => {});
    api.get(`/api/ponds/${id}/tasks`).then(({ data }) => {
      this.setData({ tasks: (data || []).slice(0, 10) });
    }).catch(() => {});
  },
  suggest() {
    api.post(`/api/ponds/${this.data.id}/suggestions`).then(({ data }) => {
      this.setData({ suggestion: data });
      wx.showToast({ title: '已生成建议 ' + data.amount + data.unit, icon: 'none' });
    }).catch((e) => wx.showToast({ title: e.message || '生成失败', icon: 'none' }));
  },
  confirmTask() {
    const sg = this.data.suggestion;
    if (!sg) return;
    api.post(`/api/ponds/${this.data.id}/tasks`, {
      request_no: 'MP-' + Date.now(),
      suggestion_code: sg.code,
      confirm_amount: sg.amount,
    }).then(({ data }) => {
      wx.showToast({ title: '任务 ' + data.task_no + ' 已下发', icon: 'none' });
      this.setData({ suggestion: null });
      this.load();
    }).catch((e) => wx.showToast({ title: e.message || '下发失败', icon: 'none' }));
  },
  predict() {
    wx.showLoading({ title: '预测中…' });
    api.get(`/api/ponds/${this.data.id}/model/predict?target=growth`).then(({ data }) => {
      wx.hideLoading();
      this.setData({ predict: data });
    }).catch((e) => {
      wx.hideLoading();
      wx.showToast({ title: e.message || '暂不能预测', icon: 'none' });
    });
  },
});
