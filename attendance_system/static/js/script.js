// 古いハンバーガーメニューの初期化を防止
document.addEventListener('DOMContentLoaded', function() {
  // 古いメニュー要素が存在する場合は削除
  const oldMenuButton = document.querySelector('.hamburger-menu');
  if (oldMenuButton) {
    oldMenuButton.remove();
  }
  
  const oldMenuContent = document.getElementById('menuItems');
  if (oldMenuContent) {
    oldMenuContent.remove();
  }
});
