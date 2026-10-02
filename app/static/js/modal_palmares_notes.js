/**
 * KLASORA - Modal Palmarès & Performances Notes
 * In-Context Analytics : initialisation réactive du graphique Chart.js
 */
document.addEventListener('DOMContentLoaded', function () {
    const modalEl = document.getElementById('modalPalmaresNotes');
    if (!modalEl) return;

    let palmaresChart = null;

    modalEl.addEventListener('shown.bs.modal', function () {
        const dataScript = document.getElementById('palmaresNotesChartDataJson');
        const canvas = document.getElementById('chartPalmaresNotes');
        if (!dataScript || !canvas || typeof Chart === 'undefined') return;

        if (palmaresChart) {
            palmaresChart.resize();
            return;
        }

        let chartData;
        try {
            chartData = JSON.parse(dataScript.textContent);
        } catch (e) {
            console.error('Erreur lecture données palmarès notes:', e);
            return;
        }

        if (!chartData || !chartData.labels || chartData.labels.length === 0) return;

        const ctx = canvas.getContext('2d');
        const bgColors = chartData.couleurs && chartData.couleurs.length ? chartData.couleurs : chartData.labels.map(() => 'rgba(99, 102, 241, 0.8)');

        palmaresChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: chartData.labels,
                datasets: [{
                    label: 'Moyenne (/20)',
                    data: chartData.moyennes,
                    backgroundColor: bgColors,
                    borderColor: bgColors.map(c => c.replace('0.85', '1.0').replace('0.8', '1.0')),
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
                                const moy = context.raw || 0;
                                const reussite = chartData.taux_reussite ? chartData.taux_reussite[idx] || 0 : 0;
                                const nbNotes = chartData.nb_notes ? chartData.nb_notes[idx] || 0 : 0;
                                return [
                                    `Moyenne : ${moy.toFixed(2)} / 20`,
                                    `  • Taux réussite : ${reussite}%`,
                                    `  • Évaluations saisies : ${nbNotes}`
                                ];
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 20,
                        ticks: {
                            stepSize: 2,
                            font: { size: 11 },
                            callback: function (val) { return val + '/20'; }
                        },
                        grid: {
                            color: function (context) {
                                if (context.tick.value === 10) {
                                    return 'rgba(239, 68, 68, 0.4)'; // Ligne rouge de seuil 10/20
                                }
                                return 'rgba(0, 0, 0, 0.05)';
                            },
                            lineWidth: function (context) {
                                return context.tick.value === 10 ? 2 : 1;
                            }
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
