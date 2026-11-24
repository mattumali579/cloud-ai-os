package com.mattumali.visortracker

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.io.File
import java.nio.FloatBuffer

class Yolo26OnnxTest {
    @Test
    fun testOrtLoadModelAndRunInference() {
        val modelFile = ModelBenchmarkTest.resolveModelFile()
        if (!modelFile.exists()) {
            println("Model file not found at ${modelFile.absolutePath}")
            return
        }

        val env = OrtEnvironment.getEnvironment()
        val sessionOptions = OrtSession.SessionOptions()
        sessionOptions.setIntraOpNumThreads(2)
        val session = env.createSession(modelFile.absolutePath, sessionOptions)
        assertNotNull(session)
        assertEquals(1, session.numInputs)
        assertEquals(2, session.numOutputs)

        val inputName = session.inputNames.iterator().next()
        println("Input name: $inputName")

        // Create float buffer for 1 x 3 x 640 x 640
        val inputSize = 1 * 3 * 640 * 640
        val floatBuffer = FloatBuffer.allocate(inputSize)
        // Fill with dummy values (0.5f)
        for (i in 0 until inputSize) {
            floatBuffer.put(0.5f)
        }
        floatBuffer.rewind()

        val inputTensor = OnnxTensor.createTensor(env, floatBuffer, longArrayOf(1, 3, 640, 640))
        val results = session.run(mapOf(inputName to inputTensor))

        val out0 = results.get(0) as OnnxTensor
        val out1 = results.get(1) as OnnxTensor
        println("Output 0 info: ${out0.info}")
        println("Output 1 info: ${out1.info}")

        val shape0 = out0.info.shape
        val shape1 = out1.info.shape
        assertEquals(1L, shape0[0])
        assertEquals(116L, shape0[1])
        assertEquals(8400L, shape0[2])

        assertEquals(1L, shape1[0])
        assertEquals(32L, shape1[1])
        assertEquals(160L, shape1[2])
        assertEquals(160L, shape1[3])

        // Verify value access
        @Suppress("UNCHECKED_CAST")
        val out0Value = out0.value as Array<Array<FloatArray>>
        assertEquals(1, out0Value.size)
        assertEquals(116, out0Value[0].size)
        assertEquals(8400, out0Value[0][0].size)

        @Suppress("UNCHECKED_CAST")
        val out1Value = out1.value as Array<Array<Array<FloatArray>>>
        assertEquals(1, out1Value.size)
        assertEquals(32, out1Value[0].size)
        assertEquals(160, out1Value[0][0].size)
        assertEquals(160, out1Value[0][0][0].size)

        results.close()
        inputTensor.close()
        session.close()
        env.close()
        println("ONNX inference verified with expected multidimensional output shapes!")
    }
}
