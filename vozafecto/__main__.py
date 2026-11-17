from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .affect import apply_model, train
from .core import Config, analyze, review_candidate, save_report, write_json
from .embox import embox_export


def main():
    parser = argparse.ArgumentParser(description='EMBox Voz Afectiva · prototipo de seminario local')
    sub = parser.add_subparsers(dest='command', required=True)
    gui = sub.add_parser('gui', help='Abrir interfaz de revisión')
    gui.add_argument('--demo', action='store_true')
    run = sub.add_parser('analyze', help='Medir audio y generar candidatos revisables')
    run.add_argument('audio')
    run.add_argument('--baseline-start',type=float,required=True)
    run.add_argument('--baseline-end',type=float,required=True)
    run.add_argument('--speaker',default='hablante_1')
    run.add_argument('--single-speaker',action='store_true',help='Declaración explícita de un solo hablante')
    run.add_argument('--turns',help='CSV/JSON de turnos con start,end,speaker,text,content_note,interaction_note,overlap')
    run.add_argument('--channel',type=int,default=0)
    run.add_argument('--window',type=float,default=2.)
    run.add_argument('--step',type=float,default=.5)
    run.add_argument('--pitch-floor',type=float,default=60.)
    run.add_argument('--pitch-ceiling',type=float,default=500.)
    run.add_argument('--model',help='Modelo Ridge entrenado, opcional')
    run.add_argument('--out',type=Path,required=True)
    export = sub.add_parser('export',help='Exportar PIN aceptados al proyecto heredado de EmBox')
    export.add_argument('report',type=Path)
    export.add_argument('--out',type=Path,required=True)
    export.add_argument('--project',type=Path)
    export.add_argument('--audio-jar',type=Path,help='JAR Audio PlugIn de otra instalación, si no se descubre localmente')
    export.add_argument('--include-pending',action='store_true',help='Incluir candidatos claramente marcados, sólo para demostrar intercambio')
    review = sub.add_parser('review',help='Registrar revisión humana sin alterar el audio')
    review.add_argument('report',type=Path)
    review.add_argument('candidate_id')
    review.add_argument('--status',choices=['segment','accepted','rejected','pending'],required=True)
    review.add_argument('--author',required=True)
    review.add_argument('--comment',required=True)
    review.add_argument('--out',type=Path,required=True,help='Nuevo JSON, conserva historial')
    trainer = sub.add_parser('train',help='Entrenar baseline afectivo con corpus etiquetado externo')
    trainer.add_argument('corpus',type=Path)
    trainer.add_argument('--out',type=Path,required=True)
    trainer.add_argument('--alpha',type=float,default=10.)
    trainer.add_argument('--test-speakers',nargs='+')
    trainer.add_argument('--label-definition',default='expresión afectiva percibida, según anotación del corpus',
                         help='Documentar constructo y protocolo de etiquetas; no cambia su validez')
    args = parser.parse_args()
    try:
        if args.command=='gui':
            from .gui import App
            App(demo=args.demo).mainloop()
        elif args.command=='analyze':
            cfg = Config(window_sec=args.window,step_sec=args.step,channel=args.channel,
                         pitch_floor=args.pitch_floor,pitch_ceiling=args.pitch_ceiling)
            report = analyze(args.audio,args.baseline_start,args.baseline_end,cfg=cfg,speaker=args.speaker,
                             single_speaker=args.single_speaker,turns_path=args.turns)
            if args.model:
                apply_model(report,args.model)
            print(save_report(report,args.out))
            print(f"{len(report['windows'])} ventanas; {len(report['candidates'])} candidatos pendientes de revisión.")
        elif args.command=='export':
            report = json.loads(args.report.read_text(encoding='utf-8-sig'))
            print(json.dumps(embox_export(report,args.out,args.project,args.include_pending,audio_jar=args.audio_jar),ensure_ascii=False,indent=2))
        elif args.command=='review':
            report = json.loads(args.report.read_text(encoding='utf-8-sig'))
            review_candidate(report,args.candidate_id,args.status,args.author,args.comment)
            if args.out.resolve()==args.report.resolve():
                raise ValueError('Use un archivo de salida distinto para conservar la revisión anterior.')
            write_json(args.out,report)
            print(args.out)
        elif args.command=='train':
            model = train(args.corpus,args.out,alpha=args.alpha,test_speakers=args.test_speakers,
                          label_definition=args.label_definition)
            print(json.dumps({'model':str(args.out),'train_n':model['train_n'],'test_n':model['test_n'],
                              'metrics':model['metrics']},ensure_ascii=False,indent=2))
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
        print('Error: '+str(e),file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
