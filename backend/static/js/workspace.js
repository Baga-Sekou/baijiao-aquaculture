/* Shared presentation helpers. Business decisions remain on the server. */
function sourceCN(value) {
  return (
    {
      sim: "仿真",
      simulated: "仿真",
      real: "实测",
      measured: "实测",
      manual: "人工",
      csv: "课程数据",
    }[value] || "来源未注明"
  );
}
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("headerDate").textContent = new Intl.DateTimeFormat(
    "zh-CN",
    { month: "long", day: "numeric", weekday: "long" },
  ).format(new Date());
  const menu = document.getElementById("menuToggle"),
    sidebar = document.getElementById("sidebar");
  menu.onclick = () => {
    const on = sidebar.classList.toggle("open");
    menu.setAttribute("aria-expanded", String(on));
  };
  document.addEventListener("click", (e) => {
    if (!sidebar.contains(e.target) && !menu.contains(e.target)) {
      sidebar.classList.remove("open");
      menu.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      sidebar.classList.remove("open");
      menu.setAttribute("aria-expanded", "false");
    }
  });
});
