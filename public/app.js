(() => {
  const root = document.documentElement;
  const timezoneOffset = String(new Date().getTimezoneOffset());
  const timezoneCookie = document.cookie.split("; ").find((item) => item.startsWith("tz_offset_minutes="));
  if (!timezoneCookie || timezoneCookie.split("=")[1] !== timezoneOffset) {
    document.cookie = `tz_offset_minutes=${timezoneOffset}; Path=/; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
    location.reload();
  }

  const savedTheme = localStorage.getItem("class-event-tracker-theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = savedTheme || (prefersDark ? "dark" : "light");

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("class-event-tracker-theme", root.dataset.theme);
    });
  });

  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.toggle);
      if (!target) return;
      target.classList.toggle("hidden");
      if (!target.classList.contains("hidden")) target.querySelector("input")?.focus();
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-upload-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector('input[type="file"]');
      const button = form.querySelector('button[type="submit"]');
      const status = form.querySelector(".upload-status");
      const files = Array.from(input.files || []);
      if (!files.length) return;
      if (!form.dataset.supabaseUrl || !form.dataset.supabaseAnonKey) {
        status.textContent = "Supabase URL and anon key must be configured for uploads.";
        return;
      }
      button.disabled = true;
      const errors = [];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        status.textContent = `Uploading ${index + 1} of ${files.length}: ${file.name}`;
        try {
          if (file.size < 1 || file.size > 10 * 1024 * 1024) {
            throw new Error("file must be between 1 byte and 10 MB");
          }
          const metadata = new FormData();
          metadata.set("csrf_token", form.dataset.csrfToken);
          metadata.set("class_id", form.dataset.classId);
          metadata.set("file_name", file.name);
          metadata.set("mime_type", file.type);
          metadata.set("size_bytes", String(file.size));
          const signResponse = await fetch("/documents/upload-sign", { method: "POST", body: metadata });
          const signed = await signResponse.json().catch(() => ({}));
          if (!signResponse.ok) throw new Error(signed.message || signed.error || "Could not prepare file upload");
          let signedUrl = signed.signed_url;
          if (!/^https?:\/\//i.test(signedUrl)) {
            signedUrl = `${form.dataset.supabaseUrl.replace(/\/$/, "")}/storage/v1${signedUrl.startsWith("/") ? "" : "/"}${signedUrl}`;
          }
          const uploadResponse = await fetch(signedUrl, {
            method: "PUT",
            headers: {
              apikey: form.dataset.supabaseAnonKey,
              Authorization: `Bearer ${form.dataset.supabaseAnonKey}`,
              "Content-Type": file.type,
              "x-upsert": "false",
            },
            body: file,
          });
          if (!uploadResponse.ok) throw new Error("Supabase Storage rejected the upload");
          const finishResponse = await fetch("/documents/upload-finish", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": form.dataset.csrfToken },
            body: JSON.stringify(signed),
          });
          if (!finishResponse.ok) {
            const result = await finishResponse.json().catch(() => ({}));
            throw new Error(result.message || result.error || "Could not save document details");
          }
        } catch (error) {
          errors.push(`${file.name}: ${error instanceof Error ? error.message : "upload failed"}`);
        }
      }
      if (errors.length) {
        status.textContent = errors.join("; ");
        status.classList.add("danger-text");
      } else {
        status.textContent = "Upload complete.";
        window.location.reload();
      }
      button.disabled = false;
    });
  });
})();
