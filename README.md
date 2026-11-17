# EMBox Voz Afectiva — prueba de seminario 0.1

Productor local de análisis vocal y candidatos de revisión. El ejecutable incluye una interfaz, una demostración sintética y exportación JSON. La integración con el EmBox de escritorio reutiliza su canal `Audio PlugIn`. Es una prueba de concepto previa a la tesis.

## Abrir y demostrar

En Windows, abrir `Iniciar_Plugin.cmd` o `dist/EMBoxVozAfectiva.exe` y pulsar **Demostración sintética**. No se necesita instalar Python para el ejecutable. Ver [guía para el profesor](docs/GUIA_PROFESOR.md).

El recorrido es audio → referencia del hablante → mediciones → candidatos → revisión humana → JSON. **Amarillo** indica candidato automático; **naranja**, segmento marcado por el revisor; **rojo**, PIN aceptado; **gris**, descarte. La prioridad 1–3 es una regla exploratoria independiente del estado de revisión.

La demo es una señal armónica artificial de 24 segundos. Muestra cambios conocidos de frecuencia y amplitud, modulación y silencio. No contiene emociones ni voces humanas. Sus alertas sólo sirven para comprobar el software.

## Qué aporta esta versión

- Extracción temporal de prosodia, nivel digital, calidad vocal y espectro; medidas ausentes con `null` y motivo.
- Referencia seleccionada del mismo hablante, con estadísticas robustas y condiciones de validez explícitas.
- Selección explícita de canal; turnos e identidad recibidos como anotaciones. No se promedian voces de canales distintos.
- Cruce con texto y notas de contenido/interacción, cuando el usuario aporta segmentos temporizados.
- Evidencia por candidato, autor, comentario e historial de revisión persistente.
- JSON completo, CSV y adaptador al `project.json` real del EmBox instalado.
- Entrenamiento opcional de un baseline dimensional de activación/valencia y evaluación con hablantes reservados.

El código de Matías ya mide F0, intensidad, jitter y shimmer. Aquí se utiliza como antecedente; no se atribuye su aporte sólo a limpieza. Esta implementación se escribió en el paquete `vozafecto`, separado de las fuentes de referencia.

## Dos niveles de resultado

**Contraste descriptivo, disponible de inmediato.** Para cada característica válida se calcula `(valor − mediana_referencia) / max(1.4826·MAD, piso)`. Los pisos están en `core.SCALE_FLOORS` y en cada referencia exportada. La activación descriptiva es el promedio de los contrastes de F0 mediana y RMS, truncados a ±6. La inestabilidad descriptiva reúne las partes positivas de rango F0, jitter, shimmer y descenso de HNR, truncadas a 0–6. Son índices de ingeniería; no son probabilidades ni estimaciones psicológicas validadas.

**Predicción afectiva supervisada, opcional.** El módulo `affect.py` entrena regresión Ridge sobre etiquetas dimensionales aportadas por un corpus. Separa hablantes, ajusta imputación y escala sólo en entrenamiento, y calcula MAE, RMSE, CCC y baseline constante en test. La entrega no incluye pesos entrenados ni un corpus afectivo humano. El procedimiento está implementado y probado con etiquetas sintéticas de ingeniería; su validez afectiva requiere el estudio posterior.

Los modelos guardan configuración, semántica de etiquetas, versiones, particiones, procedencia y métricas. La inferencia identifica segmentos que se solapan con datos de entrenamiento o test y valores fuera de la escala de entrenamiento; estos diagnósticos no son intervalos de confianza calibrados. Cargar un modelo no modifica la regla de candidatos acústicos ni convierte predicciones en PIN automáticamente.

## Mediciones y parámetros

| Familia | Resultado | Condición / definición |
|---|---|---|
| Prosodia | F0 mediana, rango p10–p90, pendiente | Hz, semitonos y semitonos/s; Praat CC, sin interpolar huecos. |
| Nivel digital | RMS | `20 log10(sqrt(mean(x²)))`, referencia digital 1; dBFS, no dB SPL. Silencio exacto: `null`. |
| Calidad vocal | Jitter local, shimmer local, HNR | Porcentaje, porcentaje y dB. Jitter/shimmer requieren cobertura periódica ≥80%; siguen siendo exploratorios en habla conectada. |
| Espectro | Centroide | Magnitud STFT Hann de 25 ms, paso de cuadros configurable; mediana en cuadros con energía. |
| Espectro | Pendiente / razón de bandas | Ajuste potencia en dB frente log2(f), 500–4000 Hz; razón potencia 50–1000 / 1000–5000 Hz. Bandas no disponibles: `null`. |
| Temporalidad | Fracción de baja energía y de periodicidad | Umbral RMS fijo −45 dBFS; no equivale a VAD, pausa conversacional ni tasa de habla. |

Configuración inicial: ventana 2 s, avance 0.5 s, cuadros 0.01 s, F0 60–500 Hz. La referencia necesita al menos dos ventanas completas válidas y no solapadas: **4 segundos por defecto**. La referencia no se presume neutra. Una muestra corta no garantiza una caracterización estable de la persona.

Se bloquea el contraste si no hay suficiente periodicidad (50%), nivel suficiente o si la proporción de muestras con `abs(x) ≥ 0.999` alcanza 1%. Esta última regla detecta proximidad a saturación; no detecta todas las formas de distorsión. La cola menor que una ventana conserva medidas, pero no genera contraste ni predicción. No hay un estimador universal de ruido/SNR ni un VAD validado.

Con varias personas, el intervalo de referencia debe pertenecer íntegramente a un turno anotado del objetivo, sin otros hablantes ni solapamiento. Sólo se comparan ventanas íntegramente atribuibles a ese hablante. Un CSV puede aportar `start,end,speaker,text,content_note,interaction_note,overlap`; véase `data/plantilla_turnos.csv`. La transcripción de la entrevista recibida no contiene identidades verificadas y su ZIP no incluye el audio original.

Las alertas se activan por características con desviación robusta absoluta ≥2.5; prioridad 2 exige al menos dos familias y prioridad 3 al menos tres familias y desviación ≥4. Las familias no se consideran canales SCAnS independientes. Se fusionan sólo ventanas consecutivas y solapadas con las mismas variables y direcciones; el reporte conserva cada ventana. No se exige una duración mínima de emoción ni se atribuye causa psicológica a la desviación.

## Exportación e interoperabilidad

`analisis_voz.json` conserva configuración, origen SHA-256, referencia, mediciones, validez, contexto, candidatos, revisiones y predicciones opcionales. `mediciones.csv` facilita inspección tabular. `candidatos.json` resume las alertas con su estado.

Para EmBox se generan `project.json`, `tags_embox.json` y `procedencia_embox.json`, más una copia del WAV. Los campos nativos son `canal`, `source`, `code`, `desc`, `initTimeInMS`, `lengthInMS`. Se exportan PIN aceptados por defecto. `--include-pending` permite demostrar candidatos explícitamente identificados, sin presentarlos como revisión humana.

Abrir la **carpeta de proyecto** en EmBox. Su aplicación histórica no importa directamente `tags_embox.json`, ni incorpora el panel afectivo o los colores de este visor. El detalle completo permanece en el reporte. El adaptador acepta WAV PCM verificable; no convierte MP3/FLAC al exportar. Las rutas del proyecto se generan para la máquina actual. Al trasladarlo, generar de nuevo el proyecto para que sus rutas correspondan al destino.

Para exportar se requiere el JAR vocal de una instalación compatible. Aquí se descubre en `EMBOX_LOCAL/app/plugins`. En otra máquina, la interfaz permite seleccionar su JAR si no se encuentra automáticamente; la consola acepta `--audio-jar`. El ejecutable puede analizar y guardar JSON/CSV sin EmBox instalado. La comprobación de carga y reapertura real se documenta en `validacion/embox_gui`.

## Trabajar desde el código

Desde esta carpeta, ejecutar `Preparar_Entorno.cmd` con Python 3.12 disponible. Las dependencias directas están fijadas en `requirements.txt`; las de desarrollo, en `requirements-dev.txt` y el entorno usado, en `requirements-lock.txt`.

```powershell
.venv\Scripts\python.exe -m vozafecto gui --demo
.venv\Scripts\python.exe -m vozafecto analyze data\demo_sintetica.wav --baseline-start 0 --baseline-end 4 --speaker sintetico_A --single-speaker --out salidas\demo
.venv\Scripts\python.exe -m vozafecto review salidas\demo\analisis_voz.json C0001 --status accepted --author "Nombre revisor" --comment "Justificación de la decisión" --out salidas\demo\revision.json
.venv\Scripts\python.exe -m vozafecto export salidas\demo\revision.json --out salidas\proyecto_embox
.venv\Scripts\python.exe -m vozafecto train corpus.csv --test-speakers C --out modelo.json --label-definition "Expresión percibida; pauta y anotadores documentados en protocolo del corpus"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verificar_gui.py
```

Los intervalos etiquetados del CSV de entrenamiento se expresan en segundos y sus etiquetas `arousal,valence` en [-1,1]. Se necesitan al menos tres hablantes válidos y suficiente material para dos grupos de entrenamiento y otro de test; esos mínimos sólo evitan un flujo vacío y no justifican suficiencia estadística. La selección de datos, consentimiento, licencia y protocolo de anotación corresponden al estudio de tesis. Las ventanas que se solapan dentro de una grabación no deben usarse como evidencia de independencia. El comando no realiza selección de hiperparámetros ni validación cruzada anidada.

`Compilar.cmd` genera el ejecutable Windows. WAV/FLAC se leen con libsndfile; formatos adicionales pueden usar FFmpeg instalado, sólo como decodificador, sin normalización ni reducción de ruido.

## Evidencia y límites de la entrega

Las pruebas automáticas incluyen tonos de frecuencia conocida, silencio, saturación, estéreo, atribución de hablante, ausencia de medidas, referencia de igual duración, colas incompletas, persistencia, revisión, contrato EmBox y separación de hablantes en el pipeline supervisado. Las pruebas de GUI verifican ejecución, exportación y reapertura, sin validar afecto humano ni reproducción en el EmBox heredado.

La tesis deberá aportar corpus y anotaciones independientes, comparación frente a baselines, robustez por hablante/canal y evaluación de utilidad. No se ha demostrado rendimiento en entrevistas reales, diagnóstico, credibilidad ni detección de engaño.

Documentación científica y trazabilidad: `../fuentes_17_nov/REQUISITOS_ENTREVISTA.md`, `../fuentes_17_nov/CONTRATO_EMBOX.md` y `../ENTREGA_ACTUAL_17_NOV/informe/`.

## Referencias instrumentales

- [Parselmouth 0.4.7, API oficial](https://parselmouth.readthedocs.io/en/stable/api_reference.html).
- [Praat: análisis de voz](https://www.fon.hum.uva.nl/praat/manual/Voice.html).
- [Praat: armonicidad](https://www.fon.hum.uva.nl/praat/manual/Harmonicity.html).
- [Praat: automatización del análisis de voz](https://www.fon.hum.uva.nl/praat/manual/Voice_6__Automating_voice_analysis_with_a_script.html).

Las licencias y metadatos de las distribuciones utilizadas se incluyen en `docs/licencias/`. Este trabajo no declara originalidad sobre sus algoritmos ni sobre el núcleo de EmBox.
