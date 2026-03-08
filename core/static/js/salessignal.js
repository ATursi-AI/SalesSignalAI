/* SalesSignal AI — Frontend JS */

document.addEventListener('DOMContentLoaded', () => {
    initCountUp();
    initFadeInUp();
    initThemeIcon();
});

/* ---------- Count-up Animation for KPI Numbers ---------- */
function initCountUp() {
    const counters = document.querySelectorAll('[data-count]');
    if (!counters.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                animateCount(entry.target);
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.3 });

    counters.forEach(el => observer.observe(el));
}

function animateCount(el) {
    const target = parseInt(el.dataset.count, 10);
    const suffix = el.dataset.suffix || '';
    const duration = 1200;
    const start = performance.now();

    function update(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(eased * target);
        el.textContent = current + suffix;

        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

/* ---------- Staggered Fade-in-up ---------- */
function initFadeInUp() {
    const elements = document.querySelectorAll('.animate-on-scroll');
    if (!elements.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('animate-fade-in-up');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });

    elements.forEach(el => observer.observe(el));
}

/* ---------- Onboarding Wizard ---------- */
function initOnboarding() {
    const steps = document.querySelectorAll('.step-content');
    const dots = document.querySelectorAll('.step-dot');
    let currentStep = 1;

    window.goToStep = function(stepNum) {
        steps.forEach(s => s.classList.remove('active'));
        dots.forEach((d, i) => {
            d.classList.remove('active');
            if (i + 1 < stepNum) d.classList.add('completed');
            else d.classList.remove('completed');
        });

        const target = document.getElementById('step-' + stepNum);
        if (target) {
            target.classList.add('active');
            dots[stepNum - 1].classList.add('active');
            currentStep = stepNum;
        }
    };

    window.submitStep = function(stepNum) {
        const form = document.getElementById('onboarding-form');
        const formData = new FormData(form);
        formData.set('step', stepNum);

        fetch(form.action, {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
            }
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                if (data.redirect) {
                    window.location.href = data.redirect;
                } else if (data.next_step) {
                    goToStep(data.next_step);
                }
            }
        })
        .catch(err => console.error('Onboarding error:', err));
    };

    // Category card selection
    document.querySelectorAll('.category-card').forEach(card => {
        card.addEventListener('click', () => {
            document.querySelectorAll('.category-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            const input = document.getElementById('service_category');
            if (input) input.value = card.dataset.id;
        });
    });
}

/* ---------- Theme Toggle ---------- */
function initThemeIcon() {
    var icon = document.getElementById('theme-icon');
    if (!icon) return;
    var theme = document.documentElement.getAttribute('data-theme') || 'dark';
    icon.className = theme === 'light' ? 'bi bi-moon-stars' : 'bi bi-sun';
}

function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-theme') || 'dark';
    var next = current === 'dark' ? 'light' : 'dark';

    html.setAttribute('data-theme', next);
    document.cookie = 'theme=' + next + ';path=/;max-age=31536000;SameSite=Lax';

    var icon = document.getElementById('theme-icon');
    if (icon) icon.className = next === 'light' ? 'bi bi-moon-stars' : 'bi bi-sun';

    // Save to server if logged in
    fetch('/settings/theme/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify({ theme: next }),
    }).catch(function() {});
}

function getCsrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? m[1] : '';
}

/* ---------- Radius slider value display ---------- */
function updateRadiusValue(val) {
    const display = document.getElementById('radius-value');
    if (display) display.textContent = val + ' miles';
}
