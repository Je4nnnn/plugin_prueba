import com.emcoders.scansembox.Controllers.CanalController;
import com.emcoders.scansembox.Controllers.MainController;
import com.emcoders.scansembox.Controllers.ProyectoController;
import com.emcoders.scansembox.Models.CanalModel;
import com.emcoders.scansembox.Utils.TimelineElement;
import com.emcoders.scansembox.Utils.TimelineTag;
import javafx.animation.PauseTransition;
import javafx.application.Platform;
import javafx.embed.swing.SwingFXUtils;
import javafx.stage.Stage;
import javafx.util.Duration;
import org.json.JSONArray;
import org.json.JSONObject;
import javax.imageio.ImageIO;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.ServiceLoader;

/** Prueba técnica con controladores/FXML reales; no modifica su código. */
public class VerificarProyecto {
    static Path projectPath;
    static Path resultPath;
    static String beforeHash;

    static Object field(Object object, String name) throws Exception {
        Field field = object.getClass().getDeclaredField(name);
        field.setAccessible(true);
        return field.get(object);
    }

    static String hash(Path path) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
    }

    static void fail(Throwable error) {
        error.printStackTrace();
        try {
            JSONObject result = new JSONObject().put("status", "FAIL")
                .put("error", error.toString()).put("project", String.valueOf(projectPath));
            Files.writeString(resultPath, result.toString(2), StandardCharsets.UTF_8);
        } catch (Exception ignored) {}
        System.exit(1);
    }

    public static void main(String[] args) throws Exception {
        projectPath = Path.of(args[0]).toAbsolutePath();
        resultPath = Path.of(args[1]).toAbsolutePath();
        Path snapshotPath = Path.of(args[2]).toAbsolutePath();
        beforeHash = hash(projectPath);
        Thread watchdog = new Thread(() -> {
            try { Thread.sleep(45000); } catch (InterruptedException ignored) {}
            fail(new IllegalStateException("Tiempo máximo de carga 45 s superado."));
        }, "watchdog-propio");
        watchdog.setDaemon(true);
        watchdog.start();
        Platform.startup(() -> Platform.runLater(() -> {
            try {
                JSONObject input = new JSONObject(Files.readString(projectPath, StandardCharsets.UTF_8));
                MainController main = new MainController();
                main.init_app();
                ProyectoController project = (ProyectoController) field(main, "proyectoController");
                project.setProyectoDir(projectPath.getParent().toString());
                project.setProjectFile(input);
                project.getProyectoModel().setName(input.getString("name"));
                project.setProyectoSeleccionado(true);
                Stage stage = (Stage) field(main, "mainWindow");
                stage.setWidth(1400);
                stage.setHeight(900);
                PauseTransition wait = new PauseTransition(Duration.seconds(3));
                wait.setOnFinished(event -> {
                    try {
                        CanalController channels = (CanalController) field(main, "canalController");
                        @SuppressWarnings("unchecked")
                        ServiceLoader<CanalModel> providers = (ServiceLoader<CanalModel>) field(channels, "canalModelServiceLoader");
                        JSONArray observedTags = new JSONArray();
                        int audioProviders = 0;
                        for (CanalModel provider : providers) {
                            if (!"Audio PlugIn".equals(provider.getName())) continue;
                            audioProviders++;
                            for (TimelineTag tag : provider.getTags()) {
                                observedTags.put(new JSONObject().put("canal", tag.getCanal())
                                    .put("source", tag.getSource()).put("code", tag.getCode())
                                    .put("initTimeInMS", tag.getInitTimeInMS()).put("lengthInMS", tag.getLengthInMS())
                                    .put("width", tag.getWidth()).put("layoutX", tag.getLayoutX()));
                            }
                        }
                        if (audioProviders != 1) throw new AssertionError("Se esperaba un proveedor vocal real.");
                        JSONArray expectedTags = input.getJSONArray("tags");
                        if (observedTags.length() != expectedTags.length()) throw new AssertionError("Cantidad de etiquetas diferente.");
                        for (int i = 0; i < expectedTags.length(); i++) {
                            JSONObject expected = expectedTags.getJSONObject(i);
                            JSONObject observed = observedTags.getJSONObject(i);
                            for (String key : List.of("canal", "source", "code"))
                                if (!expected.getString(key).equals(observed.getString(key))) throw new AssertionError("Campo distinto: " + key);
                            for (String key : List.of("initTimeInMS", "lengthInMS"))
                                if (expected.getDouble(key) != observed.getDouble(key)) throw new AssertionError("Tiempo distinto: " + key);
                            if (!(observed.getDouble("width") > 0)) throw new AssertionError("Etiqueta sin ancho visual.");
                        }
                        @SuppressWarnings("unchecked")
                        List<TimelineElement> timelines = (List<TimelineElement>) field(channels, "timelineContents");
                        if (timelines.size() != 1) throw new AssertionError("Se esperaba una línea temporal.");
                        if (!beforeHash.equals(hash(projectPath))) throw new AssertionError("La carga modificó el project.json de prueba.");
                        stage.getScene().getRoot().applyCss();
                        stage.getScene().getRoot().layout();
                        ImageIO.write(SwingFXUtils.fromFXImage(stage.getScene().snapshot(null), null), "png", snapshotPath.toFile());
                        JSONObject result = new JSONObject().put("status", "PASS")
                            .put("route", "MainController.init_app -> seleccion de proyecto -> init_main/FXML -> SPI -> loadProjectSources -> loadProjectTags")
                            .put("project", projectPath.toString()).put("project_sha256_unchanged", beforeHash)
                            .put("audio_providers", audioProviders).put("timelines", timelines.size())
                            .put("source_duration_sec", timelines.get(0).getSourceLength())
                            .put("observed_tags", observedTags).put("snapshot", snapshotPath.toString())
                            .put("audio_playback_tested", false).put("affective_validity_tested", false);
                        Files.writeString(resultPath, result.toString(2), StandardCharsets.UTF_8);
                        System.out.println("PROYECTO_REAL_FXML_SPI_AUDIO_Y_TAGS_VERIFICADOS");
                        channels.shutdownAll();
                        Platform.exit();
                        System.exit(0);
                    } catch (Throwable error) { fail(error); }
                });
                wait.play();
            } catch (Throwable error) { fail(error); }
        }));
    }
}
