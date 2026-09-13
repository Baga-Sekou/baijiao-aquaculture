// 全局配置与请求封装
// 开发者工具默认连接本机；真机调试需改为电脑局域网 IP（同一 WiFi）。
// 可在开发者工具控制台设置 wx.setStorageSync('bj_api_base', 'http://127.0.0.1:5067')。
const BASE_URL = 'http://127.0.0.1:5000';
const USER_KEY = 'bj_user';

function request(method, path, data, timeout = 10000) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: (wx.getStorageSync('bj_api_base') || BASE_URL).replace(/\/$/, '') + path,
      method,
      data,
      header: { 'X-User': wx.getStorageSync(USER_KEY) || 'owner' },
      timeout,
      success(res) {
        const d = res.data || {};
        if (d.ok) resolve(d);
        else reject(d);
      },
      fail(err) { reject({ message: '网络请求失败：' + (err.errMsg || '') }); },
    });
  });
}

module.exports = {
  BASE_URL, USER_KEY,
  get: (p) => request('GET', p),
  post: (p, d, timeout) => request('POST', p, d || {}, timeout),
};
