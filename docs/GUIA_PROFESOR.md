# Guía de demostración — EMBox Voz Afectiva 0.1

**Versión de prueba de Seminario de Informática.** Permite inspeccionar medidas vocales por intervalos, compararlas con una referencia del mismo hablante, revisar candidatos y entregar un proyecto JSON a la línea de tiempo de EMBox. Incluye una ruta de entrenamiento afectivo dimensional con datos etiquetados; **no trae un modelo emocional entrenado de fábrica**.

La demostración incorporada usa una señal sintética. Sirve para mostrar el recorrido funcional y cambios acústicos conocidos, no emociones humanas ni desempeño en entrevistas.

## Recorrido sugerido de cinco minutos

1. Abrir `EMBoxVozAfectiva.exe` y pulsar **Demostración sintética**. Como alternativa desde el código, ejecutar `.venv\Scripts\python.exe iniciar.py --demo` dentro de `plugin_prueba`.
2. Esperar el mensaje **Análisis listo**. Se muestra la señal, el tono de voz estimado (**F0**, en Hz), el nivel digital (**RMS**, en dBFS) y los índices descriptivos de contraste. La banda verde indica la referencia seleccionada, inicialmente de 0 a 4 segundos.
3. Seleccionar un candidato de la tabla. El detalle muestra su inicio/final, medidas, referencia y motivos de generación. **Escuchar intervalo** reproduce esa selección. La barra inferior permite ampliar y desplazar las gráficas.
4. Escribir un nombre en **Revisor** y una justificación en la caja inmediatamente inferior. Pulsar **Marcar segmento** y luego **Aceptar PIN** para mostrar los estados de revisión. **Descartar** conserva una decisión negativa en el historial; **Reabrir** devuelve el candidato al estado pendiente. Cada acción exige autor y comentario.
5. Pulsar **Guardar análisis + CSV** y elegir una carpeta de salida. Se generan `analisis_voz.json`, `mediciones.csv` y `candidatos.json`. Abrir después `analisis_voz.json` con **Abrir análisis JSON** permite comprobar que la revisión persiste y que la fuente conserva su huella SHA-256.
6. Mantener al menos un PIN aceptado y pulsar **Exportar PIN a EmBox**. Elegir una carpeta nueva. El resultado contiene `project.json`, `tags_embox.json`, `procedencia_embox.json` y una copia WAV cuando corresponde. Abrir **la carpeta como proyecto** en EMBox; `tags_embox.json` por sí solo es un archivo de inspección.

La exportación utiliza por defecto PIN aceptados por un revisor. El núcleo histórico representa los intervalos mediante su canal de audio; su pantalla y sus colores no incorporan automáticamente este visor afectivo. El detalle científico se consulta en el nuevo visor y en los JSON de análisis/procedencia.

El ejecutable funciona de forma independiente para analizar y guardar JSON/CSV. Abrir el proyecto exportado en EMBox requiere esa aplicación y su canal vocal compatible. Si no existe `EMBOX_LOCAL`, el visor solicita seleccionar el JAR de audio de la instalación. Al cambiar de equipo se debe generar otra vez el proyecto, porque las rutas del JAR y del audio corresponden a la máquina de exportación.

## Qué explicar durante la presentación

«El plugin conserva el intervalo de origen y las condiciones de cada medida. Una alerta automática propone dónde revisar; yo decido si merece convertirse en segmento o PIN. El análisis afectivo requiere un modelo entrenado con etiquetas y evaluación independiente. La demostración sintética comprueba el recorrido del software, no reconoce emociones».

Los colores describen el trabajo del revisor: **amarillo, alerta candidata; naranja, segmento marcado; rojo, PIN aceptado**. Gris indica descarte. La prioridad numérica 1–3 resume una heurística de variación y no representa probabilidad de emoción, gravedad clínica ni veracidad.

La «activación descriptiva» combina cambios acústicos de tono y nivel respecto de la referencia. Es diferente de **arousal/valence aprendidos por un modelo**. Los scores dimensionales aprendidos se muestran solamente si se carga un modelo compatible y se interpretan según las etiquetas de su corpus.

## Prueba con una grabación real

1. Pulsar **Cargar audio**. WAV y FLAC se leen localmente; otros formatos necesitan que FFmpeg esté disponible. Para exportar al EMBox histórico se requiere WAV PCM verificable.
2. Indicar el canal desde 0: en un estéreo, 0 y 1 son canales distintos. El motor selecciona uno; no los promedia ni limpia o normaliza la señal.
3. Identificar al hablante objetivo y una referencia dentro de su intervención. Por defecto se requieren **al menos cuatro segundos**, para obtener dos ventanas no solapadas de dos segundos, con la misma duración que las ventanas que se comparan. Silencio o saturación pueden invalidarla. La referencia es descriptiva y **no se presume emocionalmente neutra**.
4. Si el archivo contiene una sola persona, marcar **Declaro un solo hablante** únicamente cuando se conozca ese dato. Con varias personas, aportar **Turnos / transcripción…** con tiempos e identidades. El programa no realiza diarización ni inventa identidades desde el texto.
5. Pulsar **Analizar**. Ventanas sin cobertura, de otro hablante, sin atribución o solapadas no se comparan como voz válida del objetivo. Las colas menores que la ventana completa tampoco se comparan con la referencia. Una medida ausente se conserva como `null`; no se convierte en 0 Hz ni en emoción neutra.

Formato mínimo de turnos CSV, con segundos sobre el audio decodificado:

```csv
id,start,end,speaker,text,content_note,interaction_note,overlap
T1,0,4,A,"Intervención del hablante A",,,false
T2,4,8,B,"Intervención del hablante B",,,false
```

También se admite JSON con una lista o una propiedad `segments` que contenga esas filas. `start` es el inicio y `end` el final, no la duración. Los intervalos deben estar dentro del audio y los nombres de hablante deben coincidir con el objetivo elegido.

La entrevista entregada el 10 de noviembre tiene transcripción automática y tiempos, pero no asignación fiable de hablantes ni audio dentro de su ZIP. Sus segmentos no deben presentarse como turnos verificados o mediciones acústicas.

## Ruta afectiva para la tesis

El entrenamiento opcional recibe un CSV `audio,start,end,speaker,arousal,valence`, con etiquetas documentadas dentro de [-1,1]. Las rutas de audio relativas se resuelven desde la carpeta del CSV. Se reservan hablantes para el test y el preprocesamiento se ajusta en entrenamiento. El modelo usa regresión Ridge y registra MAE, RMSE, CCC y error de una predicción constante como comparación.

Ejemplos por consola desde `plugin_prueba`, usando el entorno instalado:

```powershell
.venv\Scripts\python.exe -m vozafecto analyze "grabacion.wav" --baseline-start 0 --baseline-end 4 --single-speaker --speaker A --out "salidas\prueba"
.venv\Scripts\python.exe -m vozafecto train "corpus.csv" --out "modelo.json" --test-speakers C
.venv\Scripts\python.exe -m vozafecto export "salidas\prueba\analisis_voz.json" --out "salidas\proyecto_embox"
```

Después de entrenar, **Modelo afectivo entrenado…** permite seleccionar el JSON compatible en el visor. Las etiquetas deben provenir del corpus y de su protocolo de anotación; no de reglas de F0 usadas como si fueran emociones verdaderas. La mera ejecución del entrenamiento no demuestra generalización a entrevistas chilenas.

El comando de exportación admite `--include-pending` para una inspección explícita de candidatos aún no aceptados y `--project "ruta\project.json"` para generar una copia de un proyecto compatible. La interfaz gráfica exporta PIN aceptados.

## Evidencia y trabajo pendiente

Los tests acústicos usan frecuencias conocidas y verifican silencio, saturación, canal, hablantes, límites temporales y conservación de revisiones. Estos controles no estiman la exactitud emocional. La evaluación afectiva requiere corpus, definición de etiquetas, separación por hablante, comparación con baselines y pruebas de robustez/generalización.

No se ofrece detección de mentiras, diagnóstico ni certeza sobre el estado interno de una persona. El producto actual es una prueba de concepto del seminario; la tesis deberá aportar la evaluación científica del análisis afectivo y sus límites.

Los requisitos de la reunión se documentan con tiempos e identificadores en `fuentes_17_nov/REQUISITOS_ENTREVISTA.md`: análisis por canal y alertas candidatas (S79–94), estados amarillo/naranja/rojo e inicio/final (S100–106), trazabilidad (S109–110), IDs y descripción (S163–170), comunicación JSON y prueba de concepto (S262 y S282–296).
