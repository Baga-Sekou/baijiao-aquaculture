// 全局配置与请求封装
// 使用前把 BASE_URL 改成电脑局域网 IP（手机与电脑需在同一 WiFi）
const BASE_URL = 'http://127.0.0.1:5000';
const USER_KEY = 'bj_user';

function request(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + path,
      method,
      data,
      header: { 'X-User': wx.getStorageSync(USER_KEY) || 'owner' },
      timeout: 10000,
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
  post: (p, d) => request('POST', p, d || {}),
};
