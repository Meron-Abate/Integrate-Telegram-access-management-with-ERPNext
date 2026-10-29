<template>
	<Badge :label="label" :theme="theme" variant="subtle" size="md" />
</template>

<script setup>
import { Badge } from "frappe-ui";
import { computed } from "vue";

const props = defineProps({ label: String });

const THEMES = {
	green: ["Approved", "Active", "Up to date", "Joined", "Joined (admin)", "Ready", "Success"],
	orange: ["Pending Verification", "Pending Approval", "To review", "Changes pending", "Review needed", "Invite sent", "Join requested", "Updating", "Ready (can't remove members)"],
	red: ["Rejected", "Error", "Problem", "Revoked", "Failed", "Bot needs admin rights", "Removed (banned)"],
	blue: ["Info"],
};

const theme = computed(() => {
	for (const [name, labels] of Object.entries(THEMES)) if (labels.includes(props.label)) return name;
	return "gray";
});
</script>
