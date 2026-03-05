/* ───────────────────────────────────────────────────────────
   ATSchecker — Dashboard JavaScript
   ─────────────────────────────────────────────────────────── */

// ─── Tab Switching ───
function switchTab(tabName, el) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    el.classList.add('active');
}

// ─── Filter Jobs ───
function filterJobs() {
    const city = document.getElementById('filter-city').value.toLowerCase();
    const source = document.getElementById('filter-source').value.toLowerCase();
    const search = document.getElementById('filter-search').value.toLowerCase();

    document.querySelectorAll('.job-row').forEach(row => {
        const rCity = (row.dataset.city || '').toLowerCase();
        const rSource = (row.dataset.source || '').toLowerCase();
        const rTitle = (row.dataset.title || '');
        const rCompany = (row.dataset.company || '');

        let show = true;
        if (city && !rCity.includes(city)) show = false;
        if (source && !rSource.includes(source)) show = false;
        if (search && !rTitle.includes(search) && !rCompany.includes(search)) show = false;
        row.style.display = show ? '' : 'none';
    });
}

// ─── Sort Table ───
function sortTable(tableId, colIdx) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    const dir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    table.dataset.sortDir = dir;

    rows.sort((a, b) => {
        let aVal = (a.cells[colIdx]?.textContent || '').trim();
        let bVal = (b.cells[colIdx]?.textContent || '').trim();
        const aNum = parseFloat(aVal);
        const bNum = parseFloat(bVal);
        if (!isNaN(aNum) && !isNaN(bNum)) {
            return dir === 'asc' ? aNum - bNum : bNum - aNum;
        }
        return dir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });
    rows.forEach(r => tbody.appendChild(r));
}

// ─── Compile CV ───
function compileCv() {
    const btn = document.getElementById('btn-compile');
    const statusEl = document.getElementById('compile-status');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Compiling...';
    statusEl.style.display = 'block';
    statusEl.className = 'status-msg';
    statusEl.textContent = 'Compiling LaTeX...';

    fetch('/api/compile', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                pollStatus('compile');
            } else {
                statusEl.className = 'status-msg error';
                statusEl.textContent = data.error || 'Failed';
                btn.disabled = false;
                btn.innerHTML = 'Compile LaTeX';
            }
        })
        .catch(err => {
            statusEl.className = 'status-msg error';
            statusEl.textContent = err.message;
            btn.disabled = false;
            btn.innerHTML = 'Compile LaTeX';
        });
}

// ─── Scrape Jobs ───
function startScrape(useCache) {
    const btn = document.getElementById('btn-scrape');
    const statusEl = document.getElementById('scrape-status');
    const logEl = document.getElementById('scrape-log');

    const cityChecks = Array.from(document.querySelectorAll('input[name="city"]:checked'));
    const selectedCities = cityChecks.map(c => c.value);
    const newCity = (document.getElementById('city-new')?.value || '').trim();
    if (newCity) selectedCities.push(newCity);

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scraping...';
    statusEl.style.display = 'block';
    statusEl.className = 'status-msg';
    statusEl.textContent = useCache ? 'Loading cached jobs...' : 'Starting job search (this may take several minutes)...';

    if (logEl) {
        logEl.style.display = 'block';
        logEl.textContent = '';
    }

    fetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_cache: useCache, cities: selectedCities }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                pollStatus('scrape');
                pollLogs();
            } else {
                statusEl.className = 'status-msg error';
                statusEl.textContent = data.error || 'Failed';
                btn.disabled = false;
                btn.innerHTML = 'Scrape Fresh Jobs';
            }
        })
        .catch(err => {
            statusEl.className = 'status-msg error';
            statusEl.textContent = err.message;
            btn.disabled = false;
            btn.innerHTML = 'Scrape Fresh Jobs';
        });
}

// ─── Upload PDF ───
document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('upload-form');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            const fileInput = document.getElementById('pdf-upload');
            if (!fileInput.files.length) return;

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);

            const statusEl = document.getElementById('compile-status');
            statusEl.style.display = 'block';
            statusEl.className = 'status-msg';
            statusEl.textContent = 'Uploading and analyzing PDF...';

            fetch('/api/upload', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        statusEl.className = 'status-msg success';
                        statusEl.textContent = data.message;
                        setTimeout(() => location.reload(), 1500);
                    } else {
                        statusEl.className = 'status-msg error';
                        statusEl.textContent = data.error || 'Upload failed';
                    }
                })
                .catch(err => {
                    statusEl.className = 'status-msg error';
                    statusEl.textContent = err.message;
                });
        });
    }
});

// ─── Poll Background Tasks ───
function pollStatus(task) {
    const interval = setInterval(() => {
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                const info = data[task];
                const statusEl = document.getElementById(task + '-status');
                if (!statusEl) { clearInterval(interval); return; }

                if (info && !info.running) {
                    clearInterval(interval);
                    statusEl.className = 'status-msg success';
                    statusEl.textContent = info.message;
                    setTimeout(() => location.reload(), 1500);
                } else if (info) {
                    statusEl.textContent = info.message;
                }
            })
            .catch(() => clearInterval(interval));
    }, 2000);
}

// ─── Poll Scrape Logs ───
function pollLogs() {
    const logEl = document.getElementById('scrape-log');
    if (!logEl) return;
    logEl.style.display = 'block';

    const render = () => {
        fetch('/api/scrape/logs')
            .then(r => r.json())
            .then(data => {
                if (data.logs) {
                    logEl.textContent = data.logs.join('\n');
                    logEl.scrollTop = logEl.scrollHeight;
                }
            })
            .catch(() => {});
    };

    const handle = setInterval(() => {
        render();
    }, 2000);

    // Render immediately
    render();
    // Stop polling after 5 minutes to avoid runaway timers
    setTimeout(() => clearInterval(handle), 5 * 60 * 1000);
}
