document.addEventListener('DOMContentLoaded', function() {
  const menuButton = document.querySelector('.hamburger-menu');
  const menuContent = document.getElementById('menuItems');
  
  if (menuButton && menuContent) {
    menuButton.addEventListener('click', function() {
      if (menuContent.classList.contains('show')) {
        menuContent.classList.remove('show');
      } else {
        menuContent.classList.add('show');
      }
    });
  } else {
    console.error('メニュー要素が見つかりません');
  }
});
