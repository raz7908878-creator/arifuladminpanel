// main.js - Core Javascript functions for License Manager Dashboard

// Toast Notification System
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) {
        const div = document.createElement('div');
        div.id = 'toast-container';
        document.body.appendChild(div);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = '<i class="fas fa-info-circle" style="color: var(--info)"></i>';
    if (type === 'success') {
        icon = '<i class="fas fa-check-circle" style="color: var(--success)"></i>';
    } else if (type === 'danger') {
        icon = '<i class="fas fa-exclamation-circle" style="color: var(--danger)"></i>';
    } else if (type === 'warning') {
        icon = '<i class="fas fa-exclamation-triangle" style="color: var(--warning)"></i>';
    }
    
    toast.innerHTML = `
        ${icon}
        <div class="toast-message">${message}</div>
    `;
    
    document.getElementById('toast-container').appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'toastSlideIn 0.3s reverse forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Copy to Clipboard
function copyToClipboard(text, btn = null) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('License key copied to clipboard!', 'success');
        if (btn) {
            const originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-check"></i>';
            btn.classList.add('btn-success');
            setTimeout(() => {
                btn.innerHTML = originalHTML;
                btn.classList.remove('btn-success');
            }, 1000);
        }
    }).catch(err => {
        showToast('Failed to copy to clipboard', 'danger');
    });
}

// Modal Management
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

// Close modal when clicking outside
window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        e.target.style.display = 'none';
    }
});

// CSRF helper
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// Mobile sidebar toggle
document.addEventListener('DOMContentLoaded', () => {
    const toggleBtn = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener('click', () => {
            sidebar.classList.toggle('active');
        });
    }
});
