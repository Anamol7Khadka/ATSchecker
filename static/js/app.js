/* ───────────────────────────────────────────────────────────
   ATSchecker — Dashboard JavaScript
   ─────────────────────────────────────────────────────────── */

const SORT_STORAGE_PREFIX = 'ats-sort:';

function getActiveTabName() {
    const active = document.querySelector('.tab-content.active');
    return active ? active.id.replace('tab-', '') : 'all';
}

// ─── Tab Switching ───
function switchTab(tabName, el) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + tabName).classList.add('active');
    el.classList.add('active');
    applyStoredSort(tabName);
    filterJobs();
}

// ─── Filter Jobs ───
function filterJobs() {
    const city = document.getElementById('filter-city').value.toLowerCase();
    const source = document.getElementById('filter-source').value.toLowerCase();
    const search = document.getElementById('filter-search').value.toLowerCase();
    const activeTab = document.querySelector('.tab-content.active');

    if (!activeTab) return;

    activeTab.querySelectorAll('.job-row').forEach(row => {
        const rCity = (row.dataset.city || '').toLowerCase();
        const rSource = (row.dataset.source || '').toLowerCase();
        const rTitle = (row.dataset.title || '').toLowerCase();
        const rCompany = (row.dataset.company || '').toLowerCase();

        let show = true;
        if (city && !rCity.includes(city)) show = false;
        if (source && !rSource.includes(source)) show = false;
        if (search && !rTitle.includes(search) && !rCompany.includes(search)) show = false;
        row.style.display = show ? '' : 'none';
    });

    activeTab.querySelectorAll('.group-body').forEach(groupBody => {
        const hasVisibleRows = Array.from(groupBody.querySelectorAll('.job-row')).some(row => row.style.display !== 'none');
        groupBody.style.display = hasVisibleRows ? '' : 'none';
    });
}

// ─── Sort Table ───
function setGroupBy(value) {
    const url = new URL(window.location.href);
    url.searchParams.set('group', value);
    window.location.assign(url.toString());
}

function parseDateValue(value) {
    if (!value) return 0;
    const text = String(value).trim();
    if (/^\d+$/.test(text)) {
        let ts = parseInt(text, 10);
        if (text.length === 13) ts = Math.floor(ts / 1000);
        return ts * 1000;
    }
    const parsed = Date.parse(text);
    return Number.isNaN(parsed) ? 0 : parsed;
}

function compareDatasetValues(a, b, type, dir) {
    if (type === 'number') {
        const left = parseFloat(a || '0');
        const right = parseFloat(b || '0');
        return dir === 'asc' ? left - right : right - left;
    }
    if (type === 'date') {
        const left = parseDateValue(a);
        const right = parseDateValue(b);
        return dir === 'asc' ? left - right : right - left;
    }
    const left = String(a || '');
    const right = String(b || '');
    return dir === 'asc' ? left.localeCompare(right) : right.localeCompare(left);
}

function updateSortIndicators(table, field, dir) {
    table.querySelectorAll('th').forEach(th => {
        th.classList.remove('sorted-asc', 'sorted-desc');
    });
    table.querySelectorAll('th').forEach(th => {
        const onclick = th.getAttribute('onclick') || '';
        if (onclick.includes("'" + field + "'")) {
            th.classList.add(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');
        }
    });
}

function renumberTable(table) {
    if (!table) return;
    let index = 1;
    table.querySelectorAll('tbody.group-body').forEach(body => {
        body.querySelectorAll('tr.job-row').forEach(row => {
            const firstCell = row.cells[0];
            if (!firstCell) return;
            firstCell.textContent = index;
            row.dataset.row = String(index);
            index += 1;
        });
    });

    if (index === 1) {
        table.querySelectorAll('tbody tr.job-row').forEach(row => {
            const firstCell = row.cells[0];
            if (!firstCell) return;
            firstCell.textContent = index;
            row.dataset.row = String(index);
            index += 1;
        });
    }
}

function saveSortState(tabName, field, type, dir) {
    localStorage.setItem(SORT_STORAGE_PREFIX + tabName, JSON.stringify({ field, type, dir }));
}

function loadSortState(tabName) {
    try {
        return JSON.parse(localStorage.getItem(SORT_STORAGE_PREFIX + tabName) || 'null');
    } catch (_) {
        return null;
    }
}

function applySort(tableId, field, type, dir, persist) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const groupedBodies = Array.from(table.querySelectorAll('tbody.group-body'));

    const sortRows = function (container, rows) {
        rows.sort((a, b) => compareDatasetValues(a.dataset[field], b.dataset[field], type, dir));
        rows.forEach(row => container.appendChild(row));
    };

    if (groupedBodies.length) {
        groupedBodies.forEach(body => {
            sortRows(body, Array.from(body.querySelectorAll('tr.job-row')));
        });
    } else {
        const tbody = table.querySelector('tbody');
        if (!tbody) return;
        sortRows(tbody, Array.from(tbody.querySelectorAll('tr.job-row')));
    }

    table.dataset.sortField = field;
    table.dataset.sortDir = dir;

    if (persist) {
        saveSortState(tableId.replace('table-', ''), field, type, dir);
    }
    renumberTable(table);
    updateSortIndicators(table, field, dir);
    filterJobs();
}

function applyStoredSort(tabName) {
    const tableId = 'table-' + tabName;
    const table = document.getElementById(tableId);
    if (!table) return;

    const state = loadSortState(tabName);
    if (state && state.field && state.type && state.dir) {
        applySort(tableId, state.field, state.type, state.dir, false);
        return;
    }
    updateSortIndicators(table, table.dataset.sortField || '', table.dataset.sortDir || '');
}

function sortTable(tableId, field, type, defaultDir) {
    const table = document.getElementById(tableId);
    if (!table) return;

    let dir = defaultDir || 'desc';
    if (table.dataset.sortField === field) {
        dir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    }
    applySort(tableId, field, type, dir, true);
}

// ─── Add City from Dropdown ───
function addCity() {
    const dd = document.getElementById('city-dropdown');
    const val = dd.value;
    if (!val) return;

    // Skip if already exists
    const existing = Array.from(document.querySelectorAll('input[name="city"]'));
    if (existing.some(cb => cb.value === val)) {
        dd.value = '';
        return;
    }

    const container = document.getElementById('city-checkboxes');
    const label = document.createElement('label');
    label.className = 'checkbox-inline city-added';
    label.innerHTML = '<input type="checkbox" name="city" value="' + escHtml(val) + '" checked> ' + escHtml(val);
    container.appendChild(label);
    dd.value = '';
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
let _liveJobsInterval = null;
let _logsInterval = null;

function startScrape(useCache) {
    const btn = document.getElementById('btn-scrape');
    const statusEl = document.getElementById('scrape-status');
    const logEl = document.getElementById('scrape-log');

    const cityChecks = Array.from(document.querySelectorAll('input[name="city"]:checked'));
    const selectedCities = cityChecks.map(c => c.value);

    if (!useCache && selectedCities.length === 0) {
        alert('Please select at least one city.');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scraping...';
    statusEl.style.display = 'block';
    statusEl.className = 'status-msg';
    statusEl.textContent = useCache ? 'Loading cached jobs...' : 'Starting job search (this may take several minutes)...';

    if (logEl) {
        logEl.style.display = 'block';
        logEl.textContent = '';
    }

    // Show live jobs container for fresh scrapes
    const liveContainer = document.getElementById('live-jobs-container');
    if (liveContainer && !useCache) {
        liveContainer.style.display = 'block';
        document.getElementById('live-jobs-body').innerHTML = '';
        document.getElementById('live-job-count').textContent = '0';
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
                if (!useCache) pollLiveJobs();
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
    formatAllPostedDates();
    applyStoredSort(getActiveTabName());
    filterJobs();

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

                // Update navbar job count live
                const jobsInd = document.getElementById('jobs-indicator');
                if (jobsInd && data.job_count !== undefined) {
                    jobsInd.textContent = 'Jobs: ' + data.job_count;
                    jobsInd.className = 'status-badge ' + (data.job_count > 0 ? 'status-ok' : 'status-none');
                }

                if (info && !info.running) {
                    clearInterval(interval);
                    if (_logsInterval) { clearInterval(_logsInterval); _logsInterval = null; }
                    statusEl.className = 'status-msg success';
                    statusEl.textContent = info.message;

                    // Re-enable scrape button
                    var scrapeBtn = document.getElementById('btn-scrape');
                    if (scrapeBtn) { scrapeBtn.disabled = false; scrapeBtn.innerHTML = 'Scrape Fresh Jobs'; }

                    // Do one final live-jobs fetch, then stop polling
                    if (_liveJobsInterval) {
                        clearInterval(_liveJobsInterval);
                        _liveJobsInterval = null;
                        // Final fetch to grab any remaining jobs
                        fetch('/api/jobs').then(r => r.json()).then(d => {
                            _renderLiveBatch(d.jobs, true);
                            // Show a refresh link instead of auto-reloading
                            var note = document.getElementById('live-done-note');
                            if (!note) {
                                note = document.createElement('div');
                                note.id = 'live-done-note';
                                note.style.cssText = 'margin-top:10px;text-align:center;';
                                var container = document.getElementById('live-jobs-container');
                                if (container) container.appendChild(note);
                            }
                            note.innerHTML = '<span style="color:var(--accent-green);font-weight:600;">Scraping complete \u2014 ' + d.total + ' jobs found.</span> ' +
                                '<button class="btn btn-sm btn-success" onclick="analyzeJobs()" style="margin-left:10px;">\u2728 Sort &amp; Analyze</button>' +
                                '<button class="btn btn-sm btn-secondary" onclick="location.reload()" style="margin-left:6px;">Refresh page</button>';
                        }).catch(() => { });
                    } else {
                        // Compile or cache-load — safe to reload
                        setTimeout(() => location.reload(), 1500);
                    }
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
            .catch(() => { });
    };

    render();
    _logsInterval = setInterval(render, 2000);
    setTimeout(() => { if (_logsInterval) clearInterval(_logsInterval); }, 10 * 60 * 1000);
}

// ─── Render a batch of jobs into the live table ───
var _liveRenderedCount = 0;

function _renderLiveBatch(jobs, fullReplace) {
    var tbody = document.getElementById('live-jobs-body');
    var countEl = document.getElementById('live-job-count');
    if (!tbody || !countEl) return;

    if (fullReplace || jobs.length < _liveRenderedCount) {
        // Server re-sorted or deduped — full redraw
        tbody.innerHTML = '';
        _liveRenderedCount = 0;
    }

    for (var i = _liveRenderedCount; i < jobs.length; i++) {
        var j = jobs[i];
        var badges = '<span class="badge badge-source">' + escHtml(j.source) + '</span>';
        if (j.recent) badges += ' <span class="badge badge-new">NEW</span>';
        if (j.interesting) badges += ' <span class="badge badge-interesting">INTERESTING</span>';
        if (j.precious) badges += ' <span class="badge badge-precious">PRECIOUS</span>';
        if (j.quality_score) badges += ' <span class="badge badge-quality">Q' + escHtml(j.quality_score) + '</span>';
        var matchCell = j.match > 0
            ? '<span style="color:var(--accent-green);font-weight:600;">' + j.match + '%</span>'
            : '<span style="color:var(--text-muted);">—</span>';
        var tr = document.createElement('tr');
        tr.innerHTML =
            '<td>' + (i + 1) + '</td>' +
            '<td><a href="/job/' + escHtml(j.id) + '" class="job-title-link">' + escHtml(j.title) + '</a></td>' +
            '<td>' + escHtml(j.company) + '</td>' +
            '<td>' + escHtml(j.location) + '</td>' +
            '<td>' + badges + '</td>' +
            '<td>' + matchCell + '</td>' +
            '<td style="font-size:0.78rem;">' + escHtml(j.posted_date) + '</td>';
        tbody.appendChild(tr);
    }
    _liveRenderedCount = jobs.length;
    countEl.textContent = jobs.length;
}

// ─── Poll Live Jobs (real-time updates during scrape) ───
function pollLiveJobs() {
    var container = document.getElementById('live-jobs-container');
    if (!container) return;
    _liveRenderedCount = 0;

    var render = function () {
        fetch('/api/jobs')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _renderLiveBatch(data.jobs, false);
            })
            .catch(function () { });
    };

    render();
    _liveJobsInterval = setInterval(render, 2000);
}

// ─── HTML-escape helper ───
function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
}

// ─── Sort & Analyze Jobs ───
function analyzeJobs() {
    var note = document.getElementById('live-done-note');
    if (note) {
        note.innerHTML = '<span class="spinner"></span> <span style="color:var(--accent-blue);">Analyzing and sorting jobs by match score + recency...</span>';
    }
    fetch('/api/analyze-jobs', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === 'success') {
                if (note) {
                    note.innerHTML = '<span style="color:var(--accent-green);font-weight:600;">' + data.message + '</span>';
                }
                // Re-fetch sorted jobs and redraw the live table
                fetch('/api/jobs').then(function (r) { return r.json(); }).then(function (d) {
                    _renderLiveBatch(d.jobs, true);
                    // Reload page after a brief delay to show full dashboard with match scores
                    setTimeout(function () { location.reload(); }, 2000);
                });
            } else {
                if (note) {
                    note.innerHTML = '<span style="color:var(--accent-red);">' + (data.error || 'Analysis failed') + '</span>' +
                        ' <button class="btn btn-sm btn-secondary" onclick="location.reload()" style="margin-left:6px;">Refresh page</button>';
                }
            }
        })
        .catch(function (err) {
            if (note) {
                note.innerHTML = '<span style="color:var(--accent-red);">Error: ' + escHtml(err.message) + '</span>' +
                    ' <button class="btn btn-sm btn-secondary" onclick="location.reload()" style="margin-left:6px;">Refresh page</button>';
            }
        });
}

// ─── Safe Quit ───
function safeQuit() {
    if (!confirm('Shut down the server? You will need to restart it to use the dashboard again.')) return;
    fetch('/api/shutdown', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            document.body.innerHTML = '<div style="text-align:center;padding:60px;color:#aaa;font-family:sans-serif;">' +
                '<h1>Server stopped</h1><p>The port has been freed. You can close this tab.</p>' +
                '<p style="margin-top:20px;font-size:0.85rem;color:#888;">To restart: <code>python app.py</code></p></div>';
        })
        .catch(() => {
            document.body.innerHTML = '<div style="text-align:center;padding:60px;color:#aaa;font-family:sans-serif;">' +
                '<h1>Server stopped</h1><p>You can close this tab.</p></div>';
        });
}
