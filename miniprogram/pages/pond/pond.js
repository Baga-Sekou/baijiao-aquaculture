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
                       time: data[k].collected_at,
                       expired: data[k].expired }));
      this.setData({ env });
    }).catch((e) => {
      wx.showToast({ title: e.message || '环境数据获取失败', icon: 'none' });
    });
    api.get(`/api/ponds/${id}/tasks`).then(({ data }) => {
      this.setData({ tasks: (data || []).slice(0, 10) });
    }).catch(() => {});
  },
  suggest(event) {
    if (this.data.suggesting) return;
    const mode = event.currentTarget.dataset.mode || 'llm';
    this.setData({ suggesting: true, suggestion: null, suggestError: '' });
    wx.showLoading({ title: mode === 'llm' ? '大模型生成中…' : '规则计算中…' });
    api.post(`/api/ponds/${this.data.id}/suggestions`, { mode }, 45000).then(({ data }) => {
      this.setData({ suggestion: data });
    }).catch((e) => {
      this.setData({ suggestError: e.message || '生成失败' });
    }).finally(() => {
      this.setData({ suggesting: false });
      wx.hideLoading();
    });
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
  stopTask(event) {
    const tn = event.currentTarget.dataset.task;
    wx.showModal({
      title: '请求停止投喂', editable: true, placeholderText: '填写停止原因',
      success: (r) => {
        if (!r.confirm || !r.content || !r.content.trim()) return;
        api.post(`/api/tasks/${tn}/stop`, { reason: r.content.trim() })
          .then(() => { wx.showToast({ title: '等待设备停止反馈', icon: 'none' }); this.load(); })
          .catch((e) => wx.showToast({ title: e.message || '请求失败', icon: 'none' }));
      },
    });
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
