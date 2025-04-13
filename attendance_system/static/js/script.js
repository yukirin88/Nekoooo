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
let sleepChart;

function changePeriod(period) {
    fetch(`/api/sleep_data?period=${period}`)
        .then(response => response.json())
        .then(data => {
            updateChart(data, period);
            updateTable(data, period);
        });
}

function updateChart(data, period) {
    const ctx = document.getElementById('sleepChart').getContext('2d');
    
    if (sleepChart) {
        sleepChart.destroy();
    }
    
    sleepChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(item => item.label),
            datasets: [{
                label: getPeriodLabel(period),
                data: data.map(item => item.duration),
                borderColor: 'rgba(75, 192, 192, 1)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: '睡眠時間（時間）'
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: getPeriodLabel(period)
                    }
                }
            }
        }
    });
}

function getPeriodLabel(period) {
    switch(period) {
        case 'daily': return '日付';
        case 'weekly': return '週';
        case 'monthly': return '月';
        case 'all': return '期間';
    }
}

function updateTable(data, period) {
    const tableBody = document.getElementById('sleepDataTable');
    tableBody.innerHTML = '';
    data.forEach(item => {
        const row = document.createElement('tr');
        const hours = Math.floor(item.duration);
        const minutes = Math.round((item.duration - hours) * 60);
        row.innerHTML = `
            <td>${item.label}</td>
            <td>${hours}時間${minutes}分</td>
        `;
        tableBody.appendChild(row);
    });
}