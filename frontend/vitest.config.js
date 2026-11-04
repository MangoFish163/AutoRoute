import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
export default defineConfig({plugins:[react()],test:{environment:'jsdom',include:['src/**/*.component.test.{js,jsx}'],setupFiles:['./src/testSetup.js'],restoreMocks:true}})
