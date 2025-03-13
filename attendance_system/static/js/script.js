document.addEventListener('DOMContentLoaded', function() {
  const menuBtn = document.getElementById('menu-btn');
  const menuItems = document.querySelectorAll('.menu li a');
  
  // メニュー項目をクリックしたらメニューを閉じる
  menuItems.forEach(item => {
    item.addEventListener('click', function() {
      menuBtn.checked = false;
    });
  });
});
