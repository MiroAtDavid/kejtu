<script lang="ts">
  import { onMount } from "svelte";
  import { restoreTheme } from "$lib/design/theme";
  import { restoreUserAvatar } from "$lib/design/user-avatar";
  import NativePipeline from "$lib/features/development/NativePipeline.svelte";

  onMount(restoreTheme);
  onMount(restoreUserAvatar);
</script>

<svelte:head><title>Kejtu Next — DEVELOPMENT</title></svelte:head>
<NativePipeline />
