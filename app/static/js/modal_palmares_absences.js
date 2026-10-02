/**
 * KLASORA - Modal Palmarès & Alertes Absences
 * In-Context Analytics : initialisation réactive du graphique Chart.js
 */
document.addEventListener('DOMContentLoaded', function () {
    const modalEl = document.getElementById('modalPalmaresAbsences');
    if (!modalEl) return;

    let palmaresChart = null;

    modalEl.addEventListener('shown.bs.modal', function () {
        const dataScript = document.getElementById('palmaresChartDataJson');
        const canvas = document.getElementById('chartPalmaresAbsences');
        if (!dataScript || !canvas || typeof Chart === 'undefined') return;

        if (palmaresChart) {
            palmaresChart.resize();
            return;
        }

        let chartData;
        try {
            chartData = JSON.parse(dataScript.textContent);
        } catch (e) {
            console.error('Erreur lecture données palmarès absences:', e);
            return;
        }

        if (!chartData || !chartData.labels || chartData.labels.length === 0) return;

        const ctx = canvas.getContext('2d');
        const bgColors = chartData.is_max.map(isMax => isMax ? 'rgba(239, 68, 68, 0.85)' : 'rgba(99, 102, 241, 0.75)');
        const borderColors = chartData.is_max.map(isMax => isMax ? '#dc2626' : '#4f46e5');

        palmaresChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: chartData.labels,
                datasets: [{
                    label: 'Absences',
                    data: chartData.absences,
                    backgroundColor: bgColors,
                    borderColor: borderColors,
                    borderWidth: 1.5,
                    borderRadius: 6,
                    maxBarThickness: 32,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.95)',
                        titleColor: '#ffffff',
                        bodyColor: '#e2e8f0',
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (context) {
                                const idx = context.dataIndex;
                                const tot = context.raw || 0;
                                const just = chartData.justifiees ? (chartData.justifiees[idx] || 0) : 0;
                                const nj = chartData.non_justifiees ? (chartData.non_justifiees[idx] || 0) : 0;
                                return [
                                    `Total absences : ${tot}`,
                                    `  • Justifiées : ${just}`,
                                    `  • Non justifiées : ${nj}`
                                ];
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0,
                            font: { size: 11 }
                        },
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    x: {
                        ticks: {
                            font: { size: 11 },
                            maxRotation: 45,
                            minRotation: 0
                        },
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    });
});
