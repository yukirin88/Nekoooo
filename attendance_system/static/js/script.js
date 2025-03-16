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
// 隠しボタンの機能
function showSecretMessage() {
  var modal = document.getElementById("secretModal");
  modal.style.display = "block";
  
  // 評価基準のメッセージを設定
  document.getElementById("secretMessage").innerHTML = `
    <h3>睡眠時間の評価基準</h3>
    <ul style="text-align: left; padding-left: 20px;">
      <li>10時間半以上　寝すぎ⁉</li>
      <li>9時間以上10時間半未満　ちょい寝すぎ。</li>
      <li>7時間以上9時間未満　良好だよ</li>
      <li>6時間以上7時間未満　もうちょい寝たかも</li>
      <li>3時間半以上6時間未満　頼むこれ以上は...</li>
      <li>3時間半未満　いい加減もっと寝ろよ！！</li>
    </ul>
  `;
}

// モーダルを閉じる機能
document.addEventListener("DOMContentLoaded", function() {
  var closeBtn = document.querySelector(".close-modal");
  var modal = document.getElementById("secretModal");
  
  closeBtn.onclick = function() {
    modal.style.display = "none";
  }
  
  // モーダル外をクリックしても閉じる
  window.onclick = function(event) {
    if (event.target == modal) {
      modal.style.display = "none";
    }
  }
});
