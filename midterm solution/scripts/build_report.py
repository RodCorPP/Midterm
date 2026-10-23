"""Actualiza métricas y compila el PDF IEEE con LaTeX instalado."""
from pathlib import Path
import argparse
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', default='ID', help='Identificador para Midterm1_ID.pdf')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.id):
        parser.error('Usa letras, números, guiones o guiones bajos para el ID')
    evaluation = json.loads((ROOT / 'results/evaluation.json').read_text())
    scaling = json.loads((ROOT / 'results/scaling.json').read_text())
    rows = {(r['workload'], r['method']): r for r in evaluation['summary']}
    macros = {}
    for workload, name in [('uniform', 'Uniform'), ('constant', 'Constant'), ('outlier', 'Outlier'), ('priority_sensitive', 'Priority'), ('scale_8', 'ScaleSmall'), ('scale_24', 'ScaleLarge')]:
        for method, suffix in [('sequential', 'Seq'), ('fifo', 'Fifo'), ('critical', 'Crit')]:
            macros[name + suffix] = f"{rows[workload, method]['median_seconds']:.3f}"
    critical = rows['priority_sensitive', 'critical']['median_seconds']
    macros['PrioritySaving'] = f"{(1-critical/rows['priority_sensitive','sequential']['median_seconds'])*100:.1f}"
    macros['PriorityFifoSaving'] = f"{(1-critical/rows['priority_sensitive','fifo']['median_seconds'])*100:.1f}"
    macros['ScaleSpeedup'] = f"{rows['scale_24','critical']['speedup']:.2f}"
    macros['ScaleEfficiency'] = f"{rows['scale_24','critical']['efficiency']*100:.1f}"
    for method, name in [('sequential', 'ScaleCpuSeq'), ('critical', 'ScaleCpuCrit')]:
        macros[name] = f"{rows['scale_24',method]['median_cpu_seconds']:.3f}"
    macros['MaxRss'] = f"{max(r['max_task_rss_bytes'] for r in rows.values())/1048576:.2f}"
    match = re.search(r'Ran (\d+) tests', (ROOT / 'results/tests.txt').read_text())
    if not match:
        raise RuntimeError('Falta el registro de pruebas en results/tests.txt')
    macros['TestCount'] = match.group(1)
    for r in scaling['summary']:
        name = 'MpiSeq' if r['backend'] == 'sequential' else {2: 'MpiTwo', 4: 'MpiFour'}.get(r['workers'])
        if name:
            macros[name] = f"{r['median_seconds']:.3f}"
    folder = ROOT / 'report/paper'
    (folder / 'measurements.tex').write_text(''.join('\\newcommand{\\'+k+'}{'+v+'}\n' for k,v in macros.items()))
    compiler = shutil.which('pdflatex')
    if not compiler:
        raise RuntimeError('Instala una distribución LaTeX con pdflatex para recompilar el informe')
    for _ in range(2):
        completed = subprocess.run([compiler, '-interaction=nonstopmode', '-halt-on-error', 'main.tex'], cwd=folder, text=True, capture_output=True)
        (folder / 'build.log').write_text(completed.stdout)
        if completed.returncode:
            raise RuntimeError('LaTeX no pudo compilar; consulta report/paper/build.log')
    pages = re.search(r'MIDTERM_BODY_PAGES=(\d+)', completed.stdout)
    if not pages or int(pages.group(1)) > 4:
        raise RuntimeError('El cuerpo del informe excede cuatro páginas o no pudo comprobarse')
    target = ROOT / f'Midterm1_{args.id}.pdf'
    shutil.copyfile(folder / 'main.pdf', target)
    print(f'PDF: {target}; páginas de cuerpo: {pages.group(1)}')


if __name__ == '__main__':
    main()
